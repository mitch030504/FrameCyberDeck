#!/usr/bin/env python3
"""Unit tests for resources/frame-convert.py. Run: python3 tests/frame_convert_test.py"""
import importlib.util
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock
import pathlib
import tempfile
import unittest
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    'frame_convert', REPO_ROOT / 'resources' / 'frame-convert.py'
)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


class NormalizeRefreshRateTest(unittest.TestCase):
    def test_accepts_supported_rates(self):
        for rate in (0, 72, 80, 90, 96, 108, 120, 144):
            self.assertEqual(fc.normalize_refresh_rate(str(rate)), rate)

    def test_rejects_unsupported_rate(self):
        with self.assertRaises(RuntimeError):
            fc.normalize_refresh_rate('60')


class SettingsBytesTest(unittest.TestCase):
    def test_writes_expected_keys(self):
        self.assertEqual(
            fc.settings_bytes(100, True, True, 96),
            b'scale=1.000\ncontroller_fix=1\nfoveation_fix=1\nrefresh_rate=96\n',
        )

    def test_rejects_out_of_range_scale(self):
        with self.assertRaises(RuntimeError):
            fc.settings_bytes(49, True, True)
        with self.assertRaises(RuntimeError):
            fc.settings_bytes(201, True, True)


class IsFrameConvertedTest(unittest.TestCase):
    def _zip_with(self, tmp, name, entries):
        apk = pathlib.Path(tmp) / name
        with zipfile.ZipFile(apk, 'w') as zf:
            for entry in entries:
                zf.writestr(entry, b'')
        return apk

    def test_detects_conversion_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = 'lib/arm64-v8a/'
            apk = self._zip_with(
                tmp,
                'game.apk',
                [
                    prefix + 'libframe_settings.so',
                    prefix + 'libopenxr_loader_original.so',
                    prefix + 'libopenxr_loader_generic.so',
                ],
            )
            self.assertTrue(fc.is_frame_converted(apk))

    def test_plain_apk_is_not_converted(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = self._zip_with(tmp, 'plain.apk', ['AndroidManifest.xml'])
            self.assertFalse(fc.is_frame_converted(apk))


class CopyObbsTest(unittest.TestCase):
    def _obb(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_identical_duplicates_are_copied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            data = b'x' * 128
            self._obb(src / 'game' / 'pkg' / 'main.1.pkg.obb', data)
            # the same release nested inside itself
            self._obb(src / 'game' / 'game' / 'pkg' / 'main.1.pkg.obb', data)
            dest = pathlib.Path(tmp) / 'dest'
            count = fc.copy_obbs(src, dest, 'pkg')
            self.assertEqual(count, 1)
            self.assertEqual((dest / 'obb' / 'main.1.pkg.obb').read_bytes(), data)

    def test_conflicting_sizes_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            self._obb(src / 'a' / 'main.1.pkg.obb', b'x' * 128)
            self._obb(src / 'b' / 'main.1.pkg.obb', b'y' * 256)
            with self.assertRaises(RuntimeError):
                fc.copy_obbs(src, pathlib.Path(tmp) / 'dest', 'pkg')

    def test_ignores_unrelated_packages_and_detects_same_size_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            self._obb(src / 'own' / 'main.1.pkg.obb', b'abc')
            self._obb(src / 'other' / 'main.1.other.obb', b'xyz')
            dest = pathlib.Path(tmp) / 'dest'
            self.assertEqual(fc.copy_obbs(src, dest, 'pkg'), 1)
            self.assertFalse((dest / 'obb' / 'main.1.other.obb').exists())
            self._obb(src / 'duplicate' / 'main.1.pkg.obb', b'xyz')
            with self.assertRaises(RuntimeError):
                fc.copy_obbs(src, dest, 'pkg')

    def test_package_folder_bundles_are_copied_with_structure(self):
        # Raw-data releases (e.g. Blade & Sorcery) keep Unity bundles in a
        # package-named folder instead of classic .obb files.
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            self._obb(src / 'com.a.B' / 'data.bundle', b'x' * 64)
            self._obb(src / 'com.a.B' / 'sub' / 'extra.bin', b'y' * 32)
            dest = pathlib.Path(tmp) / 'dest'
            self.assertEqual(fc.copy_obbs(src, dest, 'com.a.B'), 2)
            self.assertEqual((dest / 'obb' / 'data.bundle').read_bytes(), b'x' * 64)
            self.assertEqual((dest / 'obb' / 'sub' / 'extra.bin').read_bytes(), b'y' * 32)


class ConverterRegressionTest(unittest.TestCase):
    def test_refuses_to_overwrite_original_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / 'game.apk'
            source.write_bytes(b'original')
            with self.assertRaisesRegex(RuntimeError, 'overwrite'):
                fc.convert(source, pathlib.Path(tmp), scale=100, controllers=True, foveation=True)
            self.assertEqual(source.read_bytes(), b'original')

    def test_windows_build_tools_use_native_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk = pathlib.Path(tmp)
            build = sdk / 'build-tools' / '36.0.0'
            build.mkdir(parents=True)
            for name in ('aapt.exe', 'zipalign.exe', 'apksigner.bat'):
                (build / name).touch()
            with mock.patch.object(fc, 'os', SimpleNamespace(name='nt')):
                self.assertEqual(fc.find_build_tools(sdk), build)
                java = pathlib.Path(tmp) / 'java.exe'
                self.assertEqual(fc.apksigner_command(build, java), [java, '-jar', build / 'lib' / 'apksigner.jar'])

    def test_already_converted_apk_keeps_verified_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'game.apk'
            output = pathlib.Path(tmp) / 'out'
            with zipfile.ZipFile(src, 'w') as zf:
                for name in ('libframe_settings.so', 'libopenxr_loader_original.so', 'libopenxr_loader_generic.so'):
                    zf.writestr('lib/arm64-v8a/' + name, b'')
            with mock.patch.object(fc, 'find_sdk', return_value=pathlib.Path(tmp)), mock.patch.object(fc, 'find_build_tools', return_value=pathlib.Path(tmp)), mock.patch.object(fc, 'run', return_value="package: name='com.test.game' versionCode='42' versionName='1.2'\napplication-label:'Test Game'"):
                result = fc.convert(src, output, scale=100, controllers=True, foveation=True)
            self.assertTrue(result['alreadyConverted'])
            self.assertEqual(result['package'], 'com.test.game')
            self.assertEqual(result['versionCode'], 42)
            self.assertEqual(json.loads((output / 'frame-conversion.json').read_text()), result)

    def test_parallel_downloads_do_not_share_temporary_files(self):
        data = b'verified tool'
        with tempfile.TemporaryDirectory() as tmp:
            destination = pathlib.Path(tmp) / 'tool'
            with mock.patch.object(fc.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(data)):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    tasks = [pool.submit(fc.download, 'https://example.test/tool', destination, hashlib.sha256(data).hexdigest()) for _ in range(2)]
                    for task in tasks:
                        task.result()
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [destination])

    def test_failed_download_preserves_cached_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = pathlib.Path(tmp) / 'tool'
            destination.write_bytes(b'cached')
            with mock.patch.object(fc.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')):
                with self.assertRaises(RuntimeError):
                    fc.download('https://example.test/tool', destination, '0' * 64)
            self.assertEqual(destination.read_bytes(), b'cached')
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [destination])


class AdapterPinTest(unittest.TestCase):
    def test_pin_is_self_consistent(self):
        self.assertEqual(len(fc.FRAMEPORT_ADAPTER_SHA256), 64)
        self.assertIn(fc.FRAMEPORT_COMMIT, fc.FRAMEPORT_ADAPTER_URL)
        self.assertTrue(
            fc.FRAMEPORT_ADAPTER_URL.endswith('/artifacts/arm64-v8a/libopenxr_loader_generic.so')
        )
        for name, digest in fc.FRAMEPORT_ARTIFACTS.items():
            self.assertEqual(len(digest), 64, name)


class FramePortRecipeTest(unittest.TestCase):
    def _recipes_file(self, tmp: str) -> pathlib.Path:
        data = {
            '_source': 'FramePort by spoopyghosty0 test fixture',
            'recipes': {
                'com.test.game': {
                    'title': 'Test Game',
                    'adapter': {
                        'pose_time_fix': 1,
                        'haptic_fix': 1,
                        'vk_shader_fix': 'aa:bb',
                        'scale': 1.7,
                    },
                    'alt_overport': ['patch_remove_unreal_force_quit'],
                },
                'com.plain.game': {'title': 'Plain', 'adapter': {'rect_clamp': 0}},
            },
        }
        path = pathlib.Path(tmp) / 'recipes.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_load_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = fc.load_recipes(self._recipes_file(tmp))
            self.assertEqual(fc.recipe_for('com.test.game', recipes)['title'], 'Test Game')
            self.assertIsNone(fc.recipe_for('com.unknown.game', recipes))

    def test_user_owned_settings_are_not_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = fc.load_recipes(self._recipes_file(tmp))
            merged = fc.recipe_adapter_settings(recipes['com.test.game'])
            self.assertEqual(merged, {'haptic_fix': '1', 'pose_time_fix': '1', 'vk_shader_fix': 'aa:bb'})

    def test_alt_overport_appends_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = fc.load_recipes(self._recipes_file(tmp))
            self.assertIsNone(fc.recipe_ovrport_patches(recipes['com.plain.game']))
            patches = fc.recipe_ovrport_patches(recipes['com.test.game'])
            self.assertEqual(patches[0], fc.DEFAULT_OVRPORT_PATCHES[0])
            self.assertEqual(patches[-1], 'patch_remove_unreal_force_quit')
            self.assertEqual(len(patches), len(set(patches)))

    def test_shim_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = fc.load_recipes(self._recipes_file(tmp))
            shims = fc.recipe_shims(recipes['com.test.game'])
            self.assertTrue(shims['vk'])
            self.assertTrue(shims['xrshim'])
            self.assertFalse(shims['zink'])
            self.assertFalse(shims['gl'])
            self.assertEqual(fc.recipe_shims(recipes['com.plain.game']), {
                'xrshim': False, 'vk': False, 'zink': False, 'gl': False
            })

    def test_settings_bytes_merges_recipe_settings(self):
        text = fc.settings_bytes(100, True, False, 90, {'pose_time_fix': '1'}).decode('utf-8')
        self.assertIn('refresh_rate=90\n', text)
        self.assertIn('pose_time_fix=1\n', text)


def build_test_elf() -> bytes:
    """A minimal ELF64 with PT_DYNAMIC and a .rodata section, for shim tests."""
    import struct

    strtab = b'\0libchild.so\0'
    rodata = b'libvulkan.so\0libopenxr_loader.so\0name-in-rodata\0'
    ehsize, phentsize, shentsize = 64, 56, 64
    dyn_off = ehsize + 2 * phentsize
    str_off = dyn_off + 4 * 16
    rodata_off = str_off + len(strtab)
    shstr_off = rodata_off + len(rodata)
    shoff = (shstr_off + len(b'\0.shstrtab\0.rodata\0') + 15) // 16 * 16
    file_size = shoff + 3 * shentsize

    eh = bytearray(ehsize)
    eh[0:4] = b'\x7fELF'
    struct.pack_into('<B', eh, 4, 2)          # ELFCLASS64
    struct.pack_into('<B', eh, 5, 1)         # little-endian
    struct.pack_into('<H', eh, 16, 3)        # ET_DYN
    struct.pack_into('<H', eh, 52, 64)       # e_ehsize
    struct.pack_into('<Q', eh, 32, ehsize)   # e_phoff
    struct.pack_into('<Q', eh, 40, shoff)    # e_shoff
    struct.pack_into('<H', eh, 54, phentsize)
    struct.pack_into('<H', eh, 56, 2)        # e_phnum
    struct.pack_into('<H', eh, 58, shentsize)
    struct.pack_into('<H', eh, 60, 3)        # e_shnum
    struct.pack_into('<H', eh, 62, 1)        # e_shstrndx

    def ph(p_type, offset, vaddr, filesz, memsz, flags):
        return struct.pack('<IIQQQQQQ', p_type, flags, offset, vaddr, vaddr, filesz, memsz, 0x1000)

    load = ph(1, 0, 0, file_size, file_size, 7)      # PT_LOAD, RWX
    dynamic = ph(2, dyn_off, dyn_off, 4 * 16, 4 * 16, 6)  # PT_DYNAMIC
    dyn = b''.join([
        struct.pack('<qQ', 1, 1),           # DT_NEEDED -> "libchild.so"
        struct.pack('<qQ', 5, str_off),     # DT_STRTAB
        struct.pack('<qQ', 10, len(strtab)),  # DT_STRSZ
        struct.pack('<qQ', 0, 0),           # DT_NULL
    ])

    shstrtab = b'\0.shstrtab\0.rodata\0'
    shstr_ndx = {'.shstrtab': 1, '.rodata': 11}

    def sh(name, sh_type, offset, size):
        return struct.pack('<IIQQQQIIQQ', shstr_ndx[name], sh_type, 0, 0,
                           offset, size, 0, 0, 1, 0)

    sections = (shentsize * b'\0'
               + sh('.shstrtab', 3, shstr_off, len(shstrtab))
               + sh('.rodata', 1, rodata_off, len(rodata)))

    blob = bytearray(file_size)
    blob[0:ehsize] = eh
    blob[ehsize:ehsize + 2 * phentsize] = load + dynamic
    blob[dyn_off:dyn_off + len(dyn)] = dyn
    blob[str_off:str_off + len(strtab)] = strtab
    blob[rodata_off:rodata_off + len(rodata)] = rodata
    blob[shstr_off:shstr_off + len(shstrtab)] = shstrtab
    blob[shoff:shoff + len(sections)] = sections
    return bytes(blob)


class ElfSurgeryTest(unittest.TestCase):
    def test_replace_rodata_string(self):
        data = build_test_elf()
        patched, count = fc.elf_replace_rodata_string(data, 'libvulkan.so', 'libfp_vk.so')
        self.assertEqual(count, 1)
        self.assertIn(b'\0libfp_vk.so\0', patched)
        self.assertIn(b'libopenxr_loader.so\0', patched)  # untouched
        # a longer replacement is refused
        with self.assertRaises(ValueError):
            fc.elf_replace_rodata_string(data, 'libfp_vk.so', 'libvulkan.so')

    def test_needed_and_add_needed_roundtrip(self):
        data = build_test_elf()
        self.assertEqual(fc.elf_needed(data), ['libchild.so'])
        patched = fc.elf_add_needed(data, 'libframe_xrshim.so')
        needed = fc.elf_needed(patched)
        self.assertEqual(needed[0], 'libframe_xrshim.so')  # first: interposition order
        self.assertIn('libchild.so', needed)
        # idempotent
        self.assertEqual(fc.elf_add_needed(patched, 'libframe_xrshim.so'), patched)


if __name__ == '__main__':
    unittest.main()
