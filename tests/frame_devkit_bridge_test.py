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



# --- polish shortcut script --------------------------------------------------

import glob
import json
import os
import struct
import subprocess
import sys
import tempfile
import shutil


def vdf_encode(obj):
    out = bytearray()
    for key, value in obj.items():
        name = key.encode('utf-8', 'surrogateescape') + b'\0'
        if isinstance(value, dict):
            out += bytes([0]) + name + vdf_encode(value)
        elif isinstance(value, str):
            out += bytes([1]) + name + value.encode('utf-8', 'surrogateescape') + b'\0'
        else:
            out += bytes([2]) + name + struct.pack('<I', value & 0xFFFFFFFF)
    return bytes(out) + bytes([8])


def make_shortcut(gameid, appname, appid):
    return {
        'appid': appid,
        'AppName': appname,
        'Exe': f'/home/steamos/devkit-game/{gameid}/game.apk',
        'StartDir': f'/home/steamos/devkit-game/{gameid}',
        'Devkit': 1,
        'DevkitGameID': gameid,
    }


def run_polish(sandbox, game_dir, gameid):
    env = dict(os.environ)
    env['HOME'] = sandbox
    fakebin = os.path.join(sandbox, 'fakebin')
    env['PATH'] = fakebin + os.pathsep + env['PATH']
    result = subprocess.run(
        [sys.executable, '-c', bridge.build_polish_script(gameid, game_dir)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1]), env


class PolishShortcutTest(unittest.TestCase):
    def _sandbox(self, entries):
        tmp = tempfile.mkdtemp(prefix='polish-test-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        config = os.path.join(
            tmp, '.local/share/Steam/userdata/1234/config'
        )
        os.makedirs(config, exist_ok=True)
        with open(os.path.join(config, 'shortcuts.vdf'), 'wb') as f:
            f.write(vdf_encode({'Shortcuts': entries}))
        fakebin = os.path.join(tmp, 'fakebin')
        os.makedirs(fakebin, exist_ok=True)
        with open(os.path.join(fakebin, 'systemctl'), 'w') as f:
            f.write('#!/bin/sh\necho "$@" > "$HOME/systemctl-called"\n')
        os.chmod(os.path.join(fakebin, 'systemctl'), 0o755)
        return tmp, config

    def test_renames_and_installs_art(self):
        entries = {
            '0': make_shortcut('TestGame', 'Devkit Game: TestGame', 1111),
            '1': make_shortcut('OtherGame', 'Devkit Game: OtherGame', 2222),
        }
        tmp, config = self._sandbox(entries)

        game_dir = os.path.join(tmp, 'devkit-game-TestGame')
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, 'frame-cyberdeck.json'), 'w') as f:
            json.dump({'applicationLabel': 'Test Game Deluxe'}, f)
        with open(os.path.join(game_dir, 'library.png'), 'wb') as f:
            f.write(b'png-bytes')

        result, _env = run_polish(tmp, game_dir, 'TestGame')
        self.assertEqual(result['patched'], ['1234'])
        self.assertEqual(result['title'], 'Test Game Deluxe')

        data = open(os.path.join(config, 'shortcuts.vdf'), 'rb').read()
        self.assertIn(b'Test Game Deluxe\x00', data)
        self.assertIn(b'Devkit Game: OtherGame\x00', data)
        grid = os.path.join(config, 'grid')
        for name in ('1111p.png', '1111_hero.png'):
            self.assertEqual(open(os.path.join(grid, name), 'rb').read(), b'png-bytes')
        self.assertTrue(glob.glob(os.path.join(config, 'shortcuts.vdf.backup-*')))
        self.assertTrue(os.path.exists(os.path.join(tmp, 'systemctl-called')))
        self.assertIn('restart steam.service', open(os.path.join(tmp, 'systemctl-called')).read())

    def test_no_match_leaves_everything_alone(self):
        entries = {'0': make_shortcut('OtherGame', 'Devkit Game: OtherGame', 2222)}
        tmp, config = self._sandbox(entries)
        vdf_path = os.path.join(config, 'shortcuts.vdf')
        before = open(vdf_path, 'rb').read()

        result, _env = run_polish(tmp, os.path.join(tmp, 'nowhere'), 'MissingGame')
        self.assertEqual(result['patched'], [])
        self.assertEqual(open(vdf_path, 'rb').read(), before)
        self.assertFalse(os.path.exists(os.path.join(tmp, 'systemctl-called')))

if __name__ == '__main__':
    unittest.main()
