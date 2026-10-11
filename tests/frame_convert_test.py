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


if __name__ == '__main__':
    unittest.main()
