#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import urllib.request

DEFAULT_PORT = 32000
REQUEST_TIMEOUT = 5


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def private_key_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "steamos-devkit" / "devkit_rsa"


def properties(host: str, port: int) -> dict:
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/properties.json", timeout=REQUEST_TIMEOUT
        ) as response:
            value = json.load(response)
    except Exception as exc:
        fail(f"Could not query SteamOS Devkit service at {host}:{port}: {exc}")
    if not isinstance(value, dict):
        fail("SteamOS Devkit properties response was not a JSON object")
    return value


def ssh_command(
    host: str,
    login: str,
    command: str,
    *,
    check: bool = True,
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


def status(host: str, port: int) -> dict:
    props = properties(host, port)
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
            result = ssh_command(host, login, "true", check=False)
            paired = result.returncode == 0
            if not paired:
                pair_error = (result.stderr or result.stdout or "SSH authentication failed").strip()
        except Exception as exc:
            pair_error = str(exc)

    return {
        "ok": True,
        "host": host,
        "address": host,
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
) -> None:
    require_tools()
    name = validate_gameid(name)
    current = status(host, port)
    if not current["paired"]:
        fail(
            "Steam Frame is reachable but not paired for Devkit SSH: "
            + str(current.get("pairError") or "unknown SSH error")
        )

    login = str(current["login"])
    source = pathlib.Path(directory).expanduser().resolve()
    if not source.is_dir():
        fail(f"Upload directory does not exist: {source}")
    if not (source / start_command).is_file():
        fail(f"Start command does not exist inside upload directory: {source / start_command}")

    try:
        prep = ssh_command(
            host,
            login,
            "python3 ~/devkit-utils/steamos-prepare-upload "
            f"--gameid {shlex.quote(name)} --restart-steam 0",
        )
        prepared = json.loads(prep.stdout)
        remote_user = str(prepared["user"])
        remote_directory = str(prepared["directory"])

        key = private_key_path()
        ssh_transport = " ".join(
            shlex.quote(part)
            for part in [
                "ssh",
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
                f"{remote_user}@{host}:{remote_directory.rstrip('/')}/",
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
            host,
            login,
            "python3 ~/devkit-utils/steam-client-create-shortcut "
            f"--parms {shortcut_json}",
        )
        create_result = json.loads(created.stdout)
        if "error" in create_result:
            raise RuntimeError(str(create_result["error"]))

        if start:
            ssh_command(
                host,
                login,
                "python3 ~/devkit-utils/steam-devkit-rpc run-game "
                f"gameid={shlex.quote(name)}",
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


def list_games(host: str, port: int) -> None:
    current = status(host, port)
    if not current["paired"]:
        fail(
            "Steam Frame is reachable but not paired for Devkit SSH: "
            + str(current.get("pairError") or "unknown SSH error")
        )
    result = ssh_command(
        host,
        str(current["login"]),
        "python3 ~/devkit-utils/steamos-list-games",
    )
    emit({"ok": True, "games": json.loads(result.stdout)})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Frame CyberDeck SteamOS Devkit bridge")
    p.add_argument("--host", default="frame")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("list")
    d = sub.add_parser("deploy")
    d.add_argument("--name", required=True)
    d.add_argument("--directory", required=True)
    d.add_argument("--start-command", default="game.apk")
    d.add_argument("--start", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "status":
        emit(status(args.host, args.port))
    elif args.command == "list":
        list_games(args.host, args.port)
    elif args.command == "deploy":
        deploy(
            args.host,
            args.port,
            args.name,
            args.directory,
            args.start_command,
            args.start,
        )
    else:
        fail(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
