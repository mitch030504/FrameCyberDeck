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


def build_test_elf(exported=(), imported=(), soname='libchild.so') -> bytes:
    """A minimal ELF64 with PT_DYNAMIC, .rodata, and .dynsym/.dynstr, for shim tests."""
    import struct

    exports, imports = list(exported), list(imported)
    strtab = b'\0' + soname.encode() + b'\0xrCreateSwapchain\0'
    for name in exports + imports:
        strtab += name.encode() + b'\0'
    rodata = b'libvulkan.so\0libopenxr_loader.so\0name-in-rodata\0'
    code = struct.pack('<I', 0x7140041F)  # cmp wN, #4096
    ehsize, phentsize, shentsize = 64, 56, 64
    dyn_off = ehsize + 2 * phentsize
    str_off = dyn_off + 4 * 16
    sym_off = str_off + len(strtab)
    nsyms = 2 + len(exports) + len(imports)
    code_off = sym_off + nsyms * 24
    rodata_off = code_off + len(code)
    shstr_off = rodata_off + len(rodata)
    shstrtab = b'\0.shstrtab\0.rodata\0.dynsym\0.dynstr\0'
    shoff = (shstr_off + len(shstrtab) + 15) // 16 * 16
    file_size = shoff + 5 * shentsize

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
    struct.pack_into('<H', eh, 60, 5)        # e_shnum
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
    # Elf64_Sym: st_name st_info st_other st_shndx st_value st_size
    xr_off = 1 + len(soname) + 1
    symtab = bytearray(24 * b'\0'
                       + struct.pack('<IBBHQQ', xr_off, 0x12, 0, 1, code_off, 8))  # xrCreateSwapchain
    name_base = xr_off + len('xrCreateSwapchain') + 1
    for name in exports:
        symtab += struct.pack('<IBBHQQ', name_base, 0x12, 0, 1, code_off, 8)
        name_base += len(name) + 1
    for name in imports:
        symtab += struct.pack('<IBBHQQ', name_base, 0x12, 0, 0, 0, 0)  # SHN_UNDEF: imported
        name_base += len(name) + 1
    symtab = bytes(symtab)

    shstr_ndx = {'.shstrtab': 1, '.rodata': 11, '.dynsym': 19, '.dynstr': 27}

    def sh(name, sh_type, offset, size, link=0, entsize=0):
        return struct.pack('<IIQQQQIIQQ', shstr_ndx[name], sh_type, 0, 0,
                           offset, size, link, 0, 1, entsize)

    sections = (shentsize * b'\0'
               + sh('.shstrtab', 3, shstr_off, len(shstrtab))
               + sh('.rodata', 1, rodata_off, len(rodata))
               + sh('.dynsym', 11, sym_off, len(symtab), 4, 24)
               + sh('.dynstr', 3, str_off, len(strtab)))

    blob = bytearray(file_size)
    blob[0:ehsize] = eh
    blob[ehsize:ehsize + 2 * phentsize] = load + dynamic
    blob[dyn_off:dyn_off + len(dyn)] = dyn
    blob[str_off:str_off + len(strtab)] = strtab
    blob[sym_off:sym_off + len(symtab)] = symtab
    blob[code_off:code_off + len(code)] = code
    blob[rodata_off:rodata_off + len(rodata)] = rodata
    blob[shstr_off:shstr_off + len(shstrtab)] = shstrtab
    blob[shoff:shoff + len(sections)] = sections
    return bytes(blob)


def build_test_elf_with_exports(names):
    return build_test_elf(exported=names, soname='libovrplatformloader.so')


def build_test_elf_with_imports(names):
    return build_test_elf(imported=names, soname='libgame.so')


class ElfSurgeryTest(unittest.TestCase):
    def test_replace_rodata_string(self):
        data = build_test_elf()
        patched, count = fc.elf_replace_rodata_string(data, 'libvulkan.so', 'libfp_vk.so')
        self.assertEqual(count, 1)
        self.assertIn(b'libfp_vk.so\0', patched)
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

    def test_raise_swapchain_limit(self):
        data = build_test_elf()
        fixed, count = fc.raise_swapchain_limit(data)
        self.assertEqual(count, 1)
        self.assertIsNotNone(fixed)
        expected = (0x7140041F & ~(0xFFF << 10)) | (fc.SWAPCHAIN_LIMIT_UNITS << 10)
        code_off = 176 + 64 + len(b'\0libchild.so\0xrCreateSwapchain\0') + 48
        self.assertEqual(int.from_bytes(fixed[code_off:code_off + 4], 'little'), expected)
        # a lib without the swapchain symbols is left alone
        plain, count = fc.raise_swapchain_limit(b'\x7fELF' + b'\x02\x01' + b'\0' * 200)
        self.assertIsNone(plain)
        self.assertEqual(count, 0)



def build_test_axml() -> bytes:
    """A minimal binary AndroidManifest.xml with an application element and a category INFO filter."""
    import struct

    strings = ['category', 'name', 'android.intent.category.INFO', 'application', 'debuggable']
    enc = [struct.pack('<H', len(s)) + s.encode('utf-16-le') + b'\0' for s in strings]
    pool_data = b''.join(enc)
    pool_data += b'\0' * (-len(pool_data) % 4)
    offsets = []
    pos = 0
    for e in enc:
        offsets.append(pos)
        pos += len(e)
    pool_header_size = 28
    sstart = pool_header_size + 4 * len(offsets)
    pool = struct.pack('<HHIIIIII', 0x0001, pool_header_size, sstart + len(pool_data),
                       len(offsets), 0, 0, sstart, 0)
    pool += struct.pack(f'<{len(offsets)}I', *offsets) + pool_data
    while len(pool) % 4:
        pool += b'\0'

    def attr(ns, name, raw, dtype, value):
        return struct.pack('<IIII', ns, name, raw, (dtype << 24) | 8) + struct.pack('<I', value)

    # start element: application with debuggable=true; category with name=INFO
    app_attrs = attr(1, 4, 0xFFFFFFFF, 0x12, 0xFFFFFFFF)
    cat_attrs = attr(1, 1, 2, 0x03, 2)
    body = b''
    for name_index, attrs in ((3, app_attrs), (0, cat_attrs)):
        acount = len(attrs) // 20
        chunk = struct.pack('<HHI', 0x0102, 16, 16 + 20 + len(attrs))  # header(8) + line/comment(8) + attrExt + attrs
        chunk += b'\0' * 8  # lineNumber + comment
        chunk += struct.pack('<IIHHHHHH', 0, name_index, 20, 20, acount, 0, 0, 0)  # attrExt
        body += chunk + attrs
    header = struct.pack('<HHI', 0x0003, 8, 8 + len(pool) + len(body))
    return header + pool + body


class StubLibraryTest(unittest.TestCase):
    def test_stub_library_exports(self):
        blob = fc.build_stub_library(['ovr_Test', 'ovr_MessageType_ToString'], soname='libtest.so')
        self.assertTrue(fc.elf_is_elf(blob))
        self.assertEqual(fc.elf_dyn_symbols(blob, True), {'ovr_Test', 'ovr_MessageType_ToString'})
        self.assertEqual(fc.elf_dyn_symbols(blob, False), set())

    def test_missing_ovr_symbols(self):
        loader = build_test_elf_with_exports(['ovr_Present', 'ovrMessageType_ToString'])
        game = build_test_elf_with_imports(['ovr_Present', 'ovr_Missing'])
        missing = fc.missing_ovr_symbols({
            'libovrplatformloader.so': loader,
            'libgame.so': game,
        })
        self.assertEqual(missing, {'ovr_Missing'})


class AxmlTest(unittest.TestCase):
    def test_fix_launcher_retargets_info(self):
        manifest = build_test_axml()
        self.assertEqual(fc.axml_categories(manifest), {'android.intent.category.INFO'})
        fixed = fc.axml_fix_launcher(manifest)
        self.assertIsNotNone(fixed)
        self.assertEqual(fc.axml_categories(fixed), {'android.intent.category.LAUNCHER'})
        # already fixed: no change
        self.assertIsNone(fc.axml_fix_launcher(fixed))

    def test_set_bool_attr(self):
        manifest = build_test_axml()
        fixed = fc.axml_set_bool_attr(manifest, 'application', 'debuggable', False)
        self.assertIsNotNone(fixed)


class TelemetryPatternTest(unittest.TestCase):
    def test_findclass_replaced(self):
        import struct
        data = bytearray(build_test_elf())
        # grow the file and widen the (single, RWX) PT_LOAD so the target page exists
        new_size = 0x2000
        data.extend(b'\0' * (new_size - len(data)))
        struct.pack_into('<Q', data, 64 + 32, new_size)  # p_filesz
        struct.pack_into('<Q', data, 64 + 40, new_size)  # p_memsz
        target = 0x1000
        data[target:target + len(fc.TELEMETRY_CLASS)] = fc.TELEMETRY_CLASS
        code_off = 400
        pc_page = (code_off) & ~0xFFF
        imm = (target - pc_page) >> 12
        imm21 = imm & 0x1FFFFF
        immlo = imm21 & 3
        immhi = imm21 >> 2
        adrp = 0x90000000 | (immlo << 29) | (immhi << 5) | 1
        low = target & 0xFFF
        add = 0x91000000 | (low << 10) | (1 << 5) | 1
        ldr = 0xF9400000 | (6 << 10) | (0 << 5) | 0  # ldr x0, [x0, #0x30]
        blr = 0xD63F0000 | (0 << 5)  # blr x0
        cbz = 0x34000000 | (1 << 5)  # cbz w1, ...
        for i, word in enumerate((adrp, add, ldr, blr, cbz)):
            struct.pack_into('<I', data, code_off + i * 4, word)
        patched = fc.patch_metaxr_telemetry(bytes(data))
        self.assertIsNotNone(patched)
        # blr x0 -> mov x0, xzr
        self.assertEqual(int.from_bytes(patched[code_off + 12:code_off + 16], 'little'), 0xAA1F03E0)

    def test_no_class_string_no_patch(self):
        self.assertIsNone(fc.patch_metaxr_telemetry(build_test_elf()))


if __name__ == '__main__':
    unittest.main()
