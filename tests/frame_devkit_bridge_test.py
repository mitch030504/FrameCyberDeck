#!/usr/bin/env python3
"""Unit tests for resources/frame-devkit-bridge.py. Run: python3 tests/frame_devkit_bridge_test.py"""
import importlib.util
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    'frame_devkit_bridge', REPO_ROOT / 'resources' / 'frame-devkit-bridge.py'
)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class RsyncPercentTest(unittest.TestCase):
    def test_progress2_line(self):
        # Overall progress line produced by rsync --info=progress2.
        self.assertEqual(bridge.rsync_percent('  12,345,678  45%  12.34MB/s    0:01:23'), 45)
        self.assertEqual(bridge.rsync_percent('    8,590,607,630  100%   10.20MB/s    0:11:03'), 100)
        self.assertEqual(bridge.rsync_percent('          0   0%  0.00kB/s   0:00:00'), 0)

    def test_lines_without_progress(self):
        self.assertIsNone(bridge.rsync_percent('sending incremental file list'))
        self.assertIsNone(bridge.rsync_percent('./game.apk'))
        self.assertIsNone(bridge.rsync_percent(''))


if __name__ == '__main__':
    unittest.main()
