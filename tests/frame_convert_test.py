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


class AdapterPinTest(unittest.TestCase):
    def test_pin_is_self_consistent(self):
        self.assertEqual(len(fc.FRAMEPORT_ADAPTER_SHA256), 64)
        self.assertIn(fc.FRAMEPORT_COMMIT, fc.FRAMEPORT_ADAPTER_URL)
        self.assertTrue(
            fc.FRAMEPORT_ADAPTER_URL.endswith('/artifacts/arm64-v8a/libopenxr_loader_generic.so')
        )


if __name__ == '__main__':
    unittest.main()
