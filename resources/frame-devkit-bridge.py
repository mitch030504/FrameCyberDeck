#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request

DEFAULT_PORT = 32000
REQUEST_TIMEOUT = 5
QUERY_ATTEMPTS = 3


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def private_key_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "steamos-devkit" / "devkit_rsa"


def devkit_endpoint(host: str, port: int) -> tuple[str, dict]:
    """Resolve `host` to an address that answers the devkit service.

    The hostname can resolve differently between lookups (e.g. LAN/mDNS and a
    stale tailscale entry alternate as the resolver round-robins its sources),
    so retry resolution and probe each address until one answers. Callers
    reuse the working address for SSH/rsync instead of the ambiguous name.
    """
    last_error: Exception | None = None
    for _ in range(QUERY_ATTEMPTS):
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            infos = []
        addresses = [info[4][0] for info in infos] or [host]
        for address in dict.fromkeys(addresses):
            url_host = f"[{address}]" if ":" in address else address
            try:
                with urllib.request.urlopen(
                    f"http://{url_host}:{port}/properties.json", timeout=REQUEST_TIMEOUT
                ) as response:
                    value = json.load(response)
            except Exception as exc:
                last_error = exc
                continue
            if not isinstance(value, dict):
                fail("SteamOS Devkit properties response was not a JSON object")
            return address, value
    fail(f"Could not query SteamOS Devkit service at {host}:{port}: {last_error}")


def ssh_command(
    host: str,
    login: str,
    command: str,
    *,
    check: bool = True,
    port: int = 22,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    key = private_key_path()
    if not key.is_file():
        raise RuntimeError(
            f"SteamOS Devkit key not found at {key}. Register the Frame once in the official SteamOS Devkit Client."
        )

    (pathlib.Path.home() / ".ssh").mkdir(mode=0o700, parents=True, exist_ok=True)
    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UpdateHostKeys=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={REQUEST_TIMEOUT}",
            "-p",
            str(port),
            "-i",
            str(key),
            "--",
            f"{login}@{host}",
            command,
        ],
        check=check,
        text=True,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def status(host: str, port: int, ssh_port: int = 22) -> dict:
    address, props = devkit_endpoint(host, port)
    login = str(props.get("login") or "steamos")
    key = private_key_path()
    paired = False
    pair_error = None

    if not key.is_file():
        pair_error = (
            f"SteamOS Devkit key not found at {key}. "
            "Register the Frame once in the official SteamOS Devkit Client."
        )
    elif not shutil.which("ssh"):
        pair_error = "ssh is not installed on the host"
    else:
        try:
            result = ssh_command(address, login, "true", check=False, port=ssh_port)
            paired = result.returncode == 0
            if not paired:
                pair_error = (result.stderr or result.stdout or "SSH authentication failed").strip()
        except Exception as exc:
            pair_error = str(exc)

    return {
        "ok": True,
        "host": host,
        "address": address,
        "login": login,
        "paired": paired,
        "pairError": pair_error,
    }


# Self-contained script executed on the Frame after create-shortcut: Steam
# names devkit titles "Devkit game: <gameid>" with default art. This renames
# the shortcut to the real title (from the uploaded frame-cyberdeck.json),
# installs the library artwork (grid/<appid>p + _hero from library.*), and
# restarts Steam so the edit sticks — Steam caches shortcuts.vdf in memory
# and would otherwise overwrite the file on its next write.
POLISH_SHORTCUT_TEMPLATE = '''
import json, os, struct, shutil, subprocess, glob, time

GAMEID = __GAMEID__
DIRECTORY = __DIRECTORY__


def vdf_decode(data):
    pos = 0

    def cstring():
        nonlocal pos
        end = data.index(b"\\0", pos)
        value = data[pos:end].decode("utf-8", "surrogateescape")
        pos = end + 1
        return value

    def node():
        nonlocal pos
        out = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 8:
                return out
            name = cstring()
            if t == 0:
                out[name] = node()
            elif t == 1:
                out[name] = cstring()
            elif t == 2:
                out[name] = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                raise ValueError("unexpected vdf type %d" % t)

    return node()


def vdf_encode(obj):
    out = bytearray()
    for key, value in obj.items():
        name = key.encode("utf-8", "surrogateescape") + b"\\0"
        if isinstance(value, dict):
            out += bytes([0]) + name + vdf_encode(value)
        elif isinstance(value, str):
            out += bytes([1]) + name + value.encode("utf-8", "surrogateescape") + b"\\0"
        else:
            out += bytes([2]) + name + struct.pack("<I", value & 0xFFFFFFFF)
    return bytes(out) + bytes([8])


title = GAMEID
try:
    with open(os.path.join(DIRECTORY, "frame-cyberdeck.json")) as f:
        meta = json.load(f)
    title = meta.get("title") or meta.get("applicationLabel") or GAMEID
except Exception:
    pass

art_paths = sorted(glob.glob(os.path.join(DIRECTORY, "library.*")))
steam_root = os.path.expanduser("~/.local/share/Steam")
userdata = os.path.join(steam_root, "userdata")
patched = []
if os.path.isdir(userdata):
    for uid in os.listdir(userdata):
        config = os.path.join(userdata, uid, "config")
        vdf_path = os.path.join(config, "shortcuts.vdf")
        if not os.path.isfile(vdf_path):
            continue
        try:
            with open(vdf_path, "rb") as f:
                root = vdf_decode(f.read())
        except Exception:
            continue
        entries = root.get("Shortcuts")
        if not isinstance(entries, dict):
            continue
        entry_changed = False
        found_appid = None
        for entry in entries.values():
            if not isinstance(entry, dict) or entry.get("DevkitGameID") != GAMEID:
                continue
            for key in ("AppName", "appname"):
                if key in entry:
                    entry[key] = title
                    entry_changed = True
            appid = entry.get("appid")
            if appid:
                found_appid = appid  # the shortcut Steam registered for this game
            if art_paths and appid:
                grid = os.path.join(config, "grid")
                os.makedirs(grid, exist_ok=True)
                for art in art_paths:
                    ext = os.path.splitext(art)[1] or ".png"
                    for suffix in ("p", "_hero"):
                        shutil.copyfile(art, os.path.join(grid, "%s%s%s" % (appid, suffix, ext)))
                entry_changed = True
        if entry_changed:
            shutil.copy2(vdf_path, "%s.backup-%s" % (vdf_path, time.strftime("%Y%m%d-%H%M%S")))
            for old in sorted(glob.glob(vdf_path + ".backup-*"))[:-5]:
                try:
                    os.remove(old)
                except OSError:
                    pass
            tmp = vdf_path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(vdf_encode(root))
            os.replace(tmp, vdf_path)
            patched.append(uid)

launch = os.path.join(DIRECTORY, "launch.sh")
if found_appid and os.path.isfile(launch):
    text = open(launch).read()
    patched_launch = text.replace("SteamAppId=0", "SteamAppId=%s" % found_appid, 1)
    if patched_launch != text:
        with open(launch, "w") as f:
            f.write(patched_launch)
    os.chmod(launch, 0o755)
if patched:
    subprocess.run(["systemctl", "--user", "restart", "steam.service"], check=False)
print(json.dumps({"ok": True, "patched": patched, "title": title, "appid": found_appid}))
'''


# Steam Frame launcher, generated per game. The launcher design (watchdog,
# single-instance lock, permission repair, compat env) is FramePort's by
# spoopyghosty0 (https://github.com/spoopyghosty0/frameport), GPLv3, adapted
# to this app's flat devkit-game layout. The SteamAppId line is patched with
# the real shortcut appid after Steam registers the game.
LAUNCH_SH_TEMPLATE = """#!/usr/bin/env bash
# Steam Frame launcher for {pkg}. Generated by FrameCyberDeck; launcher design by FramePort (spoopyghosty0), GPLv3.
set -euo pipefail
app_dir="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
SteamAppId=0
[[ -f "$app_dir/game.apk" ]] || {{ echo "Game files missing at $app_dir" >&2; exit 1; }}
exec 9>"$app_dir/.launch.lock"; flock -n 9 || exit 0  # one launcher per game: a second Play press is ignored
# Games can create folders without write/search permission for the app inside Lepton, which breaks saving.
fix_perms() {{ find "$app_dir/lepton-data/external" -type d \\( ! -perm -u+rwx -o ! -perm -g+rwx \\) -exec chmod u+rwx,g+rwx {{}} + 2>/dev/null || true; }}
mkdir -p "$app_dir/lepton-data/external/Android/data/{pkg}/files" "$app_dir/lepton-data/external/Android/data/{pkg}/cache" "$app_dir/lepton-shaders" 2>/dev/null || true
fix_perms
parent=$PPID
( while sleep 2 && kill -0 $$ 2>/dev/null; do fix_perms; if [[ $parent -gt 1 ]] && ! kill -0 $parent 2>/dev/null; then kill -TERM $$; fi; done ) 9>&- & permfix=$!
export SteamAppId
export STEAM_COMPAT_INSTALL_PATH="$app_dir"
export STEAM_COMPAT_DATA_PATH="$app_dir/lepton-data"
export STEAM_COMPAT_SHADER_PATH="$app_dir/lepton-shaders"
export STEAM_COMPAT_LIBRARY_PATHS="$app_dir"
if [[ -f "$app_dir/settings.conf" ]]; then export LEPTON_ENV_FRAMEBRIDGE_CONFIG="$app_dir/settings.conf"; fi
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
export IS_PARENT=true
child=''
stop() {{
    trap - EXIT INT TERM
    kill $permfix 2>/dev/null || true
    [[ -n "$child" ]] && kill -TERM -- "-$child" 2>/dev/null || true
    podman kill "lepton-steamlaunch-$SteamAppId" >/dev/null 2>&1 || true
    # Lepton's teardown can leave the container's init (catatonit) alive; it
    # reparents to the Steam reaper, which then waits on it forever and the
    # title stays "running". Poll briefly and clear whatever lands on the
    # reaper from this launch, then let the launcher exit.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        found=0
        for pid in $(ps -eo pid,ppid --no-headers 2>/dev/null | awk -v r="$PPID" -v me="$$" '$2==r && $1!=me {{print $1}}'); do
            kill -9 "$pid" 2>/dev/null || true
            found=1
        done
        [[ "$found" == 0 ]] && break
        sleep 1
    done
}}
trap stop EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
setsid {lepton_q} start >"$app_dir/launch.log" 2>&1 &
child=$!
wait "$child"
# A fresh APK install is refused while the Lepton Development session is not
# running ("To install APKs launch Lepton Development on the device"); start
# it once and retry. An abnormal end also clears the baked install, so this
# path is how the game recovers by itself.
if grep -q "To install APKs" "$app_dir/launch.log" 2>/dev/null && command -v steam >/dev/null; then
    XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" \
        steam steam://rungameid/3056000 >/dev/null 2>&1 || true
    sleep 25
    setsid {lepton_q} start >"$app_dir/launch.log" 2>&1 &
    child=$!
    wait "$child"
fi
"""


def launch_sh_text(pkg: str, lepton: str) -> str:
    """The game's launch.sh: runs the Android runtime directly with the compat env."""
    return LAUNCH_SH_TEMPLATE.format(pkg=pkg, lepton_q=shlex.quote(lepton))


def find_lepton(address: str, login: str, ssh_port: int) -> str:
    """Path of the Lepton runtime binary on the Frame (it starts the Android container)."""
    script = (
        'for c in "$HOME/.steam/steam/steamapps/common/Lepton/lepton" '
        '"$HOME/.local/share/Steam/steamapps/common/Lepton/lepton"; do '
        "[ -x \"$c\" ] && { echo \"$c\"; exit 0; }; done; exit 1"
    )
    result = ssh_command(address, login, script, port=ssh_port, check=False)
    path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not path.startswith("/"):
        raise RuntimeError(
            "Lepton is not installed on the Frame (steamapps/common/Lepton/lepton not found). "
            "Install it once from Developer Mode (Lepton / Lepton Development), then redeploy."
        )
    return path


def registered_compat_tool(address: str, login: str, ssh_port: int, name: str) -> str:
    """The compat tool this gameid is already registered with ("" when unregistered).

    Steam binds a devkit game's runtime at its first registration and never
    re-reads it: a title registered as a fauxdroid/Lepton APK shortcut cannot
    be switched to the native launcher in place, so those deploys must keep
    the APK start command.
    """
    result = ssh_command(
        address,
        login,
        "cat ~/devkit-game/%s-settings.json 2>/dev/null || true" % shlex.quote(name),
        port=ssh_port,
        check=False,
    )
    try:
        settings = json.loads(result.stdout.strip() or "{}")
    except ValueError:
        return ""
    return str(settings.get("compat_tool") or "")


def build_polish_script(gameid: str, directory: str) -> str:
    return POLISH_SHORTCUT_TEMPLATE.replace("__GAMEID__", json.dumps(gameid)).replace(
        "__DIRECTORY__", json.dumps(directory)
    )


def tar_stream(
    source: pathlib.Path,
    fileobj,
    on_progress=None,
) -> None:
    """Stream `source` as a gzipped tar into `fileobj`, reporting cumulative
    byte progress. Pure stdlib: Windows has no rsync, so the upload runs as
    a tar pipeline over the ssh connection instead."""
    files = sorted((p for p in source.rglob("*") if p.is_file()))
    total = sum(p.stat().st_size for p in files)
    sent = 0
    last_percent = -1
    with tarfile.open(fileobj=fileobj, mode="w|gz") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(source), recursive=False)
            sent += path.stat().st_size
            if on_progress and total:
                percent = min(99, int(sent * 100 / total))
                if percent != last_percent:
                    last_percent = percent
                    on_progress(percent)


def upload_directory_over_ssh(
    source: pathlib.Path,
    address: str,
    login: str,
    ssh_port: int,
    remote_directory: str,
) -> None:
    """Windows upload: tar streamed into `ssh 'mkdir -p … && tar -xzf - -C …'`."""
    key = private_key_path()
    if not key.is_file():
        raise RuntimeError(
            f"SteamOS Devkit key not found at {key}. Register the Frame once in the official SteamOS Devkit Client."
        )
    remote_cmd = f"mkdir -p {shlex.quote(remote_directory)} && tar -xzf - -C {shlex.quote(remote_directory)}"
    process = subprocess.Popen(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UpdateHostKeys=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={REQUEST_TIMEOUT}",
            "-p",
            str(ssh_port),
            "-i",
            str(key),
            "--",
            f"{login}@{address}",
            remote_cmd,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stderr_tail: list[str] = []

    def drain() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_tail.append(line.rstrip("\n"))
            del stderr_tail[:-20]

    import threading

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    assert process.stdin is not None
    try:
        tar_stream(source, process.stdin, lambda pct: emit({"ok": True, "deployProgress": pct}))
        process.stdin.close()
    finally:
        if not process.stdin.closed:
            process.stdin.close()
    returncode = process.wait()
    reader.join(timeout=5)
    if returncode != 0:
        raise RuntimeError(
            "SteamOS Devkit upload failed: " + " | ".join(stderr_tail[-5:])
        )


def rsync_percent(text: str) -> int | None:
    """Overall percentage from an rsync --info=progress2 update line, e.g.
    '  12,345,678  45%  12.34MB/s    0:01:23'. Returns the last percentage
    found, or None when the line carries no progress."""
    percents = re.findall(r"(\d+)%", text)
    return int(percents[-1]) if percents else None


def validate_gameid(value: str) -> str:
    if value in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._]+", value):
        raise RuntimeError(
            "Steam Frame shortcut game IDs may only contain letters, digits, dots and underscores; "
            f"got {value!r}"
        )
    return value


def require_tools() -> None:
    # rsync does not exist on Windows; the tar-over-ssh upload replaces it.
    required = ("ssh",) if os.name == "nt" else ("ssh", "rsync")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        fail("Missing required host tools: " + ", ".join(missing))


def deploy(
    host: str,
    port: int,
    name: str,
    directory: str,
    start_command: str,
    start: bool,
    ssh_port: int = 22,
) -> None:
    require_tools()
    name = validate_gameid(name)
    current = status(host, port, ssh_port)
    if not current["paired"]:
        fail(
            "Steam Frame is reachable but not paired for Devkit SSH: "
            + str(current.get("pairError") or "unknown SSH error")
        )

    login = str(current["login"])
    address = str(current["address"])
    source = pathlib.Path(directory).expanduser().resolve()
    if not source.is_dir():
        fail(f"Upload directory does not exist: {source}")
    legacy_tool = registered_compat_tool(address, login, ssh_port, name)
    if legacy_tool:
        # Already registered with a Steam-bound runtime (fauxdroid/Lepton):
        # keep the APK start command; the launcher cannot take over in place.
        if start_command == "launch.sh":
            log("Keeping the APK launch path: %s is already registered with compat tool %r" % (name, legacy_tool))
            start_command = "game.apk"
    elif start_command == "launch.sh":
        lepton = find_lepton(address, login, ssh_port)
        launch = source / "launch.sh"
        launch.write_text(launch_sh_text(name, lepton), encoding="utf-8")
        launch.chmod(0o755)
    if not (source / start_command).is_file():
        fail(f"Start command does not exist inside upload directory: {source / start_command}")

    try:
        prep = ssh_command(
            address,
            login,
            "python3 ~/devkit-utils/steamos-prepare-upload "
            f"--gameid {shlex.quote(name)} --restart-steam 0",
            port=ssh_port,
        )
        prepared = json.loads(prep.stdout)
        remote_user = str(prepared["user"])
        remote_directory = str(prepared["directory"])

        key = private_key_path()
        ssh_transport = " ".join(
            shlex.quote(part)
            for part in [
                "ssh",
                "-p",
                str(ssh_port),
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "UpdateHostKeys=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(key),
            ]
        )

        if os.name == "nt":
            # Windows has no rsync; stream the upload as a tar over ssh.
            upload_directory_over_ssh(source, address, login, ssh_port, remote_directory)
        else:
            rsync_args = [
                "rsync",
                "-av",
                "--info=progress2",
                "--chmod=Du=rwx,Dgo=rx,Fu=rwx,Fog=rx",
                "--delete",
                "--delete-excluded",
                "--delete-delay",
                "--checksum",
                "-e",
                ssh_transport,
                "--",
                f"{str(source).rstrip('/')}/",
                f"{remote_user}@{address}:{remote_directory.rstrip('/')}/",
            ]

            # Stream rsync's single overall-progress line and emit each new
            # percentage as a JSON progress line; the host app forwards them
            # to the UI. The deploy result remains the final line on stdout.
            process = subprocess.Popen(
                rsync_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            assert process.stdout is not None
            last_percent: int | None = None
            line = ""
            tail = ""
            while True:
                char = process.stdout.read(1)
                if not char:
                    break
                tail = (tail + char)[-2000:]
                if char in "\r\n":
                    percent = rsync_percent(line)
                    if percent is not None and percent != last_percent:
                        last_percent = percent
                        emit({"ok": True, "deployProgress": percent})
                    line = ""
                else:
                    line += char
                    if len(line) > 512:
                        line = ""
            returncode = process.wait()
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, rsync_args, output=tail)

        shortcut = {
            "gameid": name,
            "directory": remote_directory,
            "argv": [start_command],
            "env": {},
            # The Frame's steam-client-create-shortcut requires force_appid
            # (a non-empty value writes steam_appid.txt); empty matches the
            # known-working title configuration (fauxdroid, no forced appid).
            "force_appid": "",
            "settings": {
                "steam_play": "0",
                # A native launch.sh target must be registered without a compat
                # tool, or Steam wraps it in Lepton and never runs the script.
                "compat_tool": "" if start_command == "launch.sh" else legacy_tool or "fauxdroid",
            },
        }
        shortcut_json = shlex.quote(json.dumps(shortcut, separators=(",", ":")))
        created = ssh_command(
            address,
            login,
            "python3 ~/devkit-utils/steam-client-create-shortcut "
            f"--parms {shortcut_json}",
            port=ssh_port,
        )
        create_result = json.loads(created.stdout)
        if "error" in create_result:
            raise RuntimeError(str(create_result["error"]))

        # Rename the shortcut to the real title, install the library artwork,
        # and reload Steam (before an optional launch, so the game survives).
        try:
            ssh_command(
                address,
                login,
                "python3 -",
                port=ssh_port,
                stdin=build_polish_script(name, remote_directory),
            )
        except Exception as exc:
            print(
                f"[Frame Devkit] Steam shortcut polish failed for {name}: {exc}",
                file=sys.stderr,
                flush=True,
            )

        if start:
            ssh_command(
                address,
                login,
                "python3 ~/devkit-utils/steam-devkit-rpc run-game "
                f"gameid={shlex.quote(name)}",
                port=ssh_port,
            )

    except subprocess.CalledProcessError as exc:
        detail = (
            (exc.stderr.strip() if isinstance(exc.stderr, str) else "")
            or (exc.stdout.strip() if isinstance(exc.stdout, str) else "")
            or str(exc)
        )
        fail(f"SteamOS Devkit deployment command failed: {detail}")
    except Exception as exc:
        fail(str(exc))

    emit(
        {
            "ok": True,
            "name": name,
            "directory": str(source),
            "remoteDirectory": remote_directory,
            "startCommand": start_command,
            "runtime": "Android",
            "compatTool": "fauxdroid",
            "started": start,
        }
    )


def list_games_data(host: str, port: int, ssh_port: int = 22) -> tuple[dict, list[dict]]:
    current = status(host, port, ssh_port)
    if not current["paired"]:
        fail(
            "Steam Frame is reachable but not paired for Devkit SSH: "
            + str(current.get("pairError") or "unknown SSH error")
        )

    login = str(current["login"])
    address = str(current["address"])
    result = ssh_command(
        address,
        login,
        "python3 ~/devkit-utils/steamos-list-games",
        port=ssh_port,
    )
    raw_games = json.loads(result.stdout)
    games: list[dict] = []

    for raw in raw_games:
        gameid = str(raw.get("gameid") or "")
        if not gameid:
            continue

        quoted_gameid = shlex.quote(gameid)
        metadata_result = ssh_command(
            address,
            login,
            'cat "$HOME"/devkit-game/'
            + quoted_gameid
            + '/frame-cyberdeck.json 2>/dev/null || cat "$HOME"/devkit-game/'
            + quoted_gameid
            + '/frame-conversion.json 2>/dev/null || true',
            check=False,
            port=ssh_port,
        )
        metadata: dict = {}
        text = (metadata_result.stdout or "").strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    metadata = parsed
            except json.JSONDecodeError:
                pass

        managed = metadata.get("schemaVersion") == 1
        package_name = metadata.get("packageName") or metadata.get("package") or gameid
        version_code = metadata.get("versionCode", 0)
        try:
            version_code = int(version_code)
        except (TypeError, ValueError):
            version_code = 0

        games.append(
            {
                "gameid": gameid,
                "packageName": str(package_name),
                "versionCode": version_code,
                "managed": managed,
                "versionName": str(metadata.get("versionName") or ""),
                "title": str(metadata.get("title") or gameid),
                "applicationLabel": str(
                    metadata.get("applicationLabel") or metadata.get("title") or gameid
                ),
            }
        )

    return current, games


def list_games(host: str, port: int, ssh_port: int = 22) -> None:
    _, games = list_games_data(host, port, ssh_port)
    emit({"ok": True, "games": games})


def delete_game(host: str, port: int, name: str, ssh_port: int = 22) -> None:
    validate_gameid(name)
    current, games = list_games_data(host, port, ssh_port)
    if not any(game["gameid"] == name for game in games):
        fail(f"Devkit title not found: {name}")

    login = str(current["login"])
    address = str(current["address"])
    try:
        ssh_command(
            address,
            login,
            "python3 ~/devkit-utils/steamos-delete --delete-title " + shlex.quote(name),
            port=ssh_port,
        )
        quoted_name = shlex.quote(name)
        ssh_command(
            address,
            login,
            'rm -f "$HOME"/devkit-game/'
            + quoted_name
            + '-argv.json "$HOME"/devkit-game/'
            + quoted_name
            + '-settings.json',
            check=False,
            port=ssh_port,
        )
    except subprocess.CalledProcessError as exc:
        detail = (
            (exc.stderr.strip() if isinstance(exc.stderr, str) else "")
            or (exc.stdout.strip() if isinstance(exc.stdout, str) else "")
            or str(exc)
        )
        fail(f"SteamOS Devkit delete failed: {detail}")

    emit({"ok": True, "name": name})


def run_game(host: str, port: int, name: str, ssh_port: int = 22) -> None:
    validate_gameid(name)
    current, games = list_games_data(host, port, ssh_port)
    if not any(game["gameid"] == name for game in games):
        fail(f"Devkit title not found: {name}")

    try:
        ssh_command(
            str(current["address"]),
            str(current["login"]),
            "python3 ~/devkit-utils/steam-devkit-rpc run-game "
            f"gameid={shlex.quote(name)}",
            port=ssh_port,
        )
    except subprocess.CalledProcessError as exc:
        detail = (
            (exc.stderr.strip() if isinstance(exc.stderr, str) else "")
            or (exc.stdout.strip() if isinstance(exc.stdout, str) else "")
            or str(exc)
        )
        fail(f"SteamOS Devkit launch failed: {detail}")

    emit({"ok": True, "name": name})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Frame CyberDeck SteamOS Devkit bridge")
    p.add_argument("--host", default="frame")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--ssh-port", type=int, default=22)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("list")
    delete = sub.add_parser("delete")
    delete.add_argument("--name", required=True)
    run = sub.add_parser("run")
    run.add_argument("--name", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--name", required=True)
    d.add_argument("--directory", required=True)
    d.add_argument("--start-command", default="game.apk")
    d.add_argument("--start", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "status":
        emit(status(args.host, args.port, args.ssh_port))
    elif args.command == "list":
        list_games(args.host, args.port, args.ssh_port)
    elif args.command == "delete":
        delete_game(args.host, args.port, args.name, args.ssh_port)
    elif args.command == "run":
        run_game(args.host, args.port, args.name, args.ssh_port)
    elif args.command == "deploy":
        deploy(
            args.host,
            args.port,
            args.name,
            args.directory,
            args.start_command,
            args.start,
            args.ssh_port,
        )
    else:
        fail(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
