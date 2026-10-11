#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
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
) -> subprocess.CompletedProcess[str]:
    key = private_key_path()
    if not key.is_file():
        raise RuntimeError(
            f"SteamOS Devkit key not found at {key}. Register the Frame once in the official SteamOS Devkit Client."
        )

    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={REQUEST_TIMEOUT}",
            "-p",
            str(port),
            "-i",
            str(key),
            f"{login}@{host}",
            command,
        ],
        check=check,
        text=True,
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


def validate_gameid(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._]+", value):
        raise RuntimeError(
            "Steam Frame shortcut game IDs may only contain letters, digits, dots and underscores; "
            f"got {value!r}"
        )
    return value


def require_tools() -> None:
    missing = [name for name in ("ssh", "rsync") if not shutil.which(name)]
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
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(key),
            ]
        )

        subprocess.run(
            [
                "rsync",
                "-av",
                "--chmod=Du=rwx,Dgo=rx,Fu=rwx,Fog=rx",
                "--delete",
                "--delete-excluded",
                "--delete-delay",
                "--checksum",
                "-e",
                ssh_transport,
                f"{str(source).rstrip('/')}/",
                f"{remote_user}@{address}:{remote_directory.rstrip('/')}/",
            ],
            check=True,
        )

        shortcut = {
            "gameid": name,
            "directory": remote_directory,
            "argv": [start_command],
            "env": {},
            "settings": {
                "steam_play": "0",
                "compat_tool": "fauxdroid",
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
