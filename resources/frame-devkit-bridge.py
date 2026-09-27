#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from types import SimpleNamespace


def fail(message: str, code: int = 1):
    print(json.dumps({"ok": False, "error": message}), flush=True)
    raise SystemExit(code)


def load_devkit():
    pyz = os.environ.get("STEAMOS_DEVKIT_PYZ", "").strip()
    if not pyz:
        fail("STEAMOS_DEVKIT_PYZ was not supplied")
    p = pathlib.Path(pyz).expanduser().resolve()
    if not p.is_file():
        fail(f"SteamOS Devkit Client archive was not found: {p}")
    sys.path.insert(0, str(p))
    try:
        import devkit_client  # type: ignore
        import signalslot  # type: ignore
    except Exception as exc:
        fail(f"Could not import SteamOS Devkit Client from {p}: {exc}")
    return devkit_client, signalslot


def machine_args(devkit_client, host: str, port: int):
    return SimpleNamespace(
        machine=host,
        machine_name_type=devkit_client.MachineNameType.ADDRESS,
        login=None,
        http_port=port,
    )


def cmd_status(devkit_client, host: str, port: int):
    args = machine_args(devkit_client, host, port)
    machine = devkit_client.resolve_machine(
        args.machine,
        login=None,
        need_login=True,
        need_devkit1=False,
        name_type=args.machine_name_type,
        http_port=port,
    )
    if not machine.login:
        fail(f"SteamOS Devkit service at {host}:{port} did not report a login user")

    paired = False
    pair_error = None
    try:
        ssh, _client, resolved = devkit_client._open_ssh_for_args_all(args)
        try:
            paired = True
            address = resolved.address
            login = resolved.login
        finally:
            ssh.close()
    except Exception as exc:
        address = machine.address
        login = machine.login
        pair_error = str(exc)

    print(json.dumps({
        "ok": True,
        "host": host,
        "address": address,
        "login": login,
        "paired": paired,
        "pairError": pair_error,
    }), flush=True)


def cmd_register(devkit_client, host: str, port: int):
    args = machine_args(devkit_client, host, port)
    result = devkit_client.register(args)
    print(json.dumps({"ok": True, "result": result}), flush=True)


def make_upload_args(devkit_client, signalslot, host: str, port: int, name: str, directory: str, start_command: str):
    directory_path = pathlib.Path(directory).expanduser().resolve()
    if not directory_path.is_dir():
        fail(f"Upload directory does not exist: {directory_path}")
    start_path = directory_path / start_command
    if not start_path.is_file():
        fail(f"Start command does not exist inside upload directory: {start_path}")

    return SimpleNamespace(
        machine=host,
        machine_name_type=devkit_client.MachineNameType.ADDRESS,
        login=None,
        http_port=port,
        restart_steam=False,
        name=name,
        directory=str(directory_path),
        argv=[start_command],
        delete_extraneous=True,
        skip_newer_files=False,
        verify_checksums=True,
        filter_args=[],
        steam_play_debug=devkit_client.SteamPlayDebug.Disabled,
        deps={},
        cancel_signal=signalslot.Signal(),
        clear_settings=False,
        settings_file=[],
        set_json=[],
        set_keyval=["steam_play=0", "compat_tool=fauxdroid"],
    )


def cmd_deploy(devkit_client, signalslot, host: str, port: int, name: str, directory: str, start_command: str, start: bool):
    args = make_upload_args(devkit_client, signalslot, host, port, name, directory, start_command)
    result = devkit_client.new_or_ensure_game(args)
    if not result:
        fail("SteamOS Devkit Client reported that the title upload failed")

    if start:
        fake_devkit = SimpleNamespace(
            machine_command_args=(host, devkit_client.MachineNameType.ADDRESS),
            http_port=port,
        )
        devkit_client.run_game(fake_devkit, name)

    print(json.dumps({
        "ok": True,
        "name": name,
        "directory": str(pathlib.Path(directory).resolve()),
        "startCommand": start_command,
        "runtime": "Android",
        "compatTool": "fauxdroid",
        "started": start,
    }), flush=True)


def cmd_list(devkit_client, host: str, port: int):
    games = devkit_client.list_games(machine_args(devkit_client, host, port))
    print(json.dumps({"ok": True, "games": games}), flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Frame CyberDeck SteamOS Devkit bridge")
    p.add_argument("--host", default="frame")
    p.add_argument("--port", type=int, default=32000)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("register")
    sub.add_parser("list")
    d = sub.add_parser("deploy")
    d.add_argument("--name", required=True)
    d.add_argument("--directory", required=True)
    d.add_argument("--start-command", default="game.apk")
    d.add_argument("--start", action="store_true")
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[SteamOS Devkit] %(message)s")
    args = build_parser().parse_args()
    devkit_client, signalslot = load_devkit()
    try:
        if args.command == "status":
            cmd_status(devkit_client, args.host, args.port)
        elif args.command == "register":
            cmd_register(devkit_client, args.host, args.port)
        elif args.command == "list":
            cmd_list(devkit_client, args.host, args.port)
        elif args.command == "deploy":
            cmd_deploy(
                devkit_client,
                signalslot,
                args.host,
                args.port,
                args.name,
                args.directory,
                args.start_command,
                args.start,
            )
        else:
            fail(f"Unknown command: {args.command}")
    except SystemExit:
        raise
    except Exception as exc:
        logging.exception("SteamOS Devkit bridge failed")
        fail(str(exc))


if __name__ == "__main__":
    main()
