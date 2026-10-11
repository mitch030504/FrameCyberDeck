#!/usr/bin/env python3
"""Unit tests for resources/frame-convert.py. Run: python3 tests/frame_convert_test.py"""
import importlib.util
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
            count = fc.copy_obbs(src, dest)
            self.assertEqual(count, 1)
            self.assertEqual((dest / 'obb' / 'main.1.pkg.obb').read_bytes(), data)

    def test_conflicting_sizes_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            self._obb(src / 'a' / 'main.1.pkg.obb', b'x' * 128)
            self._obb(src / 'b' / 'main.1.pkg.obb', b'y' * 256)
            with self.assertRaises(RuntimeError):
                fc.copy_obbs(src, pathlib.Path(tmp) / 'dest')


class AdapterPinTest(unittest.TestCase):
    def test_pin_is_self_consistent(self):
        self.assertEqual(len(fc.FRAMEPORT_ADAPTER_SHA256), 64)
        self.assertIn(fc.FRAMEPORT_COMMIT, fc.FRAMEPORT_ADAPTER_URL)
        self.assertTrue(
            fc.FRAMEPORT_ADAPTER_URL.endswith('/artifacts/arm64-v8a/libopenxr_loader_generic.so')
        )


if __name__ == '__main__':
    unittest.main()
