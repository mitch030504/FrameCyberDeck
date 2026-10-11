#!/usr/bin/env python3
"""Unit tests for resources/frame-devkit-bridge.py. Run: python3 tests/frame_devkit_bridge_test.py"""
import importlib.util
import pathlib
import unittest
import shutil
import subprocess
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    'frame_devkit_bridge', REPO_ROOT / 'resources' / 'frame-devkit-bridge.py'
)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class SecurityTest(unittest.TestCase):
    def test_rejects_parent_game_ids(self):
        for name in ('.', '..', '../other'):
            with self.assertRaises(RuntimeError):
                bridge.validate_gameid(name)
        self.assertEqual(bridge.validate_gameid('com.test.game'), 'com.test.game')

    def test_ssh_remembers_and_checks_host_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            key = home / '.config' / 'steamos-devkit' / 'devkit_rsa'
            key.parent.mkdir(parents=True)
            key.touch()
            with mock.patch.object(bridge.pathlib.Path, 'home', return_value=home), mock.patch.object(bridge.subprocess, 'run') as run:
                bridge.ssh_command('frame.local', 'deck', 'true')
                command = run.call_args.args[0]
                self.assertIn('StrictHostKeyChecking=accept-new', command)
                self.assertEqual(command[command.index('--') + 1], 'deck@frame.local')
                self.assertNotIn('StrictHostKeyChecking=no', command)
                self.assertNotIn('UserKnownHostsFile=/dev/null', command)
                self.assertTrue((home / '.ssh').is_dir())



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

        data = (pathlib.Path(config) / 'shortcuts.vdf').read_bytes()
        self.assertIn(b'Test Game Deluxe\x00', data)
        self.assertIn(b'Devkit Game: OtherGame\x00', data)
        grid = os.path.join(config, 'grid')
        for name in ('1111p.png', '1111_hero.png'):
            self.assertEqual((pathlib.Path(grid) / name).read_bytes(), b'png-bytes')
        self.assertTrue(glob.glob(os.path.join(config, 'shortcuts.vdf.backup-*')))
        self.assertTrue(os.path.exists(os.path.join(tmp, 'systemctl-called')))
        self.assertIn('restart steam.service', (pathlib.Path(tmp) / 'systemctl-called').read_text())

    def test_no_match_leaves_everything_alone(self):
        entries = {'0': make_shortcut('OtherGame', 'Devkit Game: OtherGame', 2222)}
        tmp, config = self._sandbox(entries)
        vdf_path = os.path.join(config, 'shortcuts.vdf')
        before = pathlib.Path(vdf_path).read_bytes()

        result, _env = run_polish(tmp, os.path.join(tmp, 'nowhere'), 'MissingGame')
        self.assertEqual(result['patched'], [])
        self.assertEqual(pathlib.Path(vdf_path).read_bytes(), before)
        self.assertFalse(os.path.exists(os.path.join(tmp, 'systemctl-called')))


class TarStreamTest(unittest.TestCase):
    def test_round_trip(self):
        import io
        import tarfile
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'src'
            (src / 'sub').mkdir(parents=True)
            (src / 'game.apk').write_bytes(b'apk-bytes')
            (src / 'sub' / 'data.obb').write_bytes(b'obb-bytes' * 100)
            buffer = io.BytesIO()
            percents = []
            bridge.tar_stream(src, buffer, percents.append)
            buffer.seek(0)
            out = pathlib.Path(tmp) / 'out'
            with tarfile.open(fileobj=buffer, mode='r|gz') as tar:
                tar.extractall(out, filter='data')
            names = sorted(str(q.relative_to(out)) for q in out.rglob('*') if q.is_file())
            extracted = (out / 'sub' / 'data.obb').read_bytes()
            self.assertEqual(names, ['game.apk', 'sub/data.obb'])
            self.assertEqual(extracted, b'obb-bytes' * 100)
            self.assertEqual(percents[-1], 99)



class LaunchShTest(unittest.TestCase):
    def test_launch_sh_is_valid_bash(self):
        text = bridge.launch_sh_text('com.test.game', '/home/steamos/.steam/steam/steamapps/common/Lepton/lepton')
        self.assertTrue(text.startswith('#!/usr/bin/env bash'))
        self.assertIn("SteamAppId=0", text)  # patched with the real appid after registration
        self.assertIn("com.test.game/files", text)
        self.assertIn("LEPTON_ENV_FRAMEBRIDGE_CONFIG", text)
        self.assertIn("lepton-steamlaunch-$SteamAppId", text)
        self.assertIn("setsid /home/steamos/.steam/steam/steamapps/common/Lepton/lepton start", text)
        if shutil.which('bash'):
            proc = subprocess.run(['bash', '-n', '/dev/stdin'], input=text, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_appid_patch_replaces_placeholder_once(self):
        text = bridge.launch_sh_text('com.test.game', '/usr/bin/lepton')
        patched = text.replace('SteamAppId=0', 'SteamAppId=1234567', 1)
        self.assertIn('SteamAppId=1234567', patched)
        self.assertNotIn('SteamAppId=0', patched)
        self.assertEqual(patched.count('SteamAppId='), text.count('SteamAppId='))



class RegisteredCompatToolTest(unittest.TestCase):
    def _run(self, stdout, code=0):
        result = mock.Mock(returncode=code, stdout=stdout, stderr="")
        with mock.patch.object(bridge, "ssh_command", return_value=result) as ssh:
            tool = bridge.registered_compat_tool("frame", "steamos", 22, "com.test.game")
        return tool, ssh

    def test_registered_fauxdroid_title_is_legacy(self):
        tool, ssh = self._run('{"steam_play": "0", "compat_tool": "fauxdroid"}\n')
        self.assertEqual(tool, "fauxdroid")
        ssh.assert_called_once()

    def test_unregistered_title_returns_empty(self):
        for stdout in ("", "\n", "not json"):
            tool, _ = self._run(stdout)
            self.assertEqual(tool, "")

    def test_native_registration_returns_empty_tool(self):
        tool, _ = self._run('{"steam_play": "0", "compat_tool": ""}\n')
        self.assertEqual(tool, "")


if __name__ == '__main__':
    unittest.main()
