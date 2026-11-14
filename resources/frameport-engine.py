#!/usr/bin/env python3
"""Headless entry point for the vendored FramePort engine.

The Electron main process runs this with the managed Python environment and
talks JSON-lines: every line on stdout is one object, either an event or the
final result. Library output is redirected to stderr so it cannot corrupt the
protocol.

Usage:
    frameport-engine.py probe
    frameport-engine.py tools-status
    frameport-engine.py tools-install
    frameport-engine.py scan <path>
    frameport-engine.py build <package> [--outdir <dir>]
    frameport-engine.py show <package>
"""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
import traceback

REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), file=REAL_STDOUT, flush=True)


def add_engine_path() -> None:
    here = pathlib.Path(__file__).resolve()
    candidates = [
        here.parent.parent / "vendor" / "frameport" / "src",
        here.parent / "vendor" / "frameport" / "src",
    ]
    for candidate in candidates:
        if (candidate / "frameport").is_dir():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit(f"vendored FramePort engine not found next to {here}")


def reporter():
    from frameport.core.events import Reporter

    def sink(event) -> None:
        payload = {
            "type": "event",
            "kind": event.kind,
            "stage": event.stage,
            "message": event.message,
        }
        if event.fraction is not None:
            payload["fraction"] = event.fraction
        if event.data:
            payload["data"] = event.data
        emit(payload)

    return Reporter([sink])


def command_probe(_args) -> dict:
    import platform

    import frameport
    from frameport._version import __version__

    return {
        "ok": True,
        "engine": "frameport",
        "version": __version__,
        "python": platform.python_version(),
        "module": str(pathlib.Path(frameport.__file__).resolve().parent),
    }


def command_tools_status(_args) -> dict:
    from frameport.tools import toolchain

    return {
        "ok": True,
        "tools": [
            {
                "name": item.name,
                "installed": item.installed,
                "version": item.version,
                "path": str(item.path) if item.path else None,
                "optional": item.optional,
                "detail": item.detail,
            }
            for item in toolchain.status()
        ],
    }


def command_tools_install(_args) -> dict:
    from frameport.tools import toolchain

    def progress(message: str) -> None:
        emit({"type": "event", "kind": "log", "stage": "tools", "message": message})

    toolchain.ensure_all(progress=progress)
    return command_tools_status(None)


def command_scan(args) -> dict:
    from frameport import pipeline

    path = pathlib.Path(args.path)
    if not path.exists():
        raise RuntimeError(f"scan path does not exist: {path}")
    report = pipeline.ScanReport()
    games = pipeline.add_path(path, reporter(), only_new=False, report=report)
    items = []
    for game in games:
        items.append(
            {
                "package": game.get("package"),
                "title": game.get("title"),
                "name": game.get("name"),
                "apk": game.get("apk"),
                "data_dir": game.get("data_dir"),
                "data_bytes": game.get("data_bytes"),
                "status": (game.get("recipe") or {}).get("status"),
                "warnings": pipeline.analysis_warnings(game),
            }
        )
    return {"ok": True, "games": items}


def command_build(args) -> dict:
    from frameport import pipeline

    outdir = pathlib.Path(args.outdir).resolve() if args.outdir else None
    result = pipeline.build_game(args.package, reporter(), outdir=outdir)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "FramePort build failed")
    apk = pathlib.Path(result["apk"]).resolve()
    return {
        "ok": True,
        "package": args.package,
        "apk": str(apk),
        "output_dir": str(apk.parent),
        "sha256": result.get("sha256"),
        "checks": result.get("checks"),
        "recipe_fp": result.get("recipe_fp"),
        "source_version": result.get("source_version"),
        "overport": result.get("overport"),
    }


def command_show(args) -> dict:
    from frameport.core import library

    entry = library.game(args.package)
    if not entry:
        raise RuntimeError(f"package not in the library: {args.package}")
    return {
        "ok": True,
        "package": args.package,
        "title": entry.get("title"),
        "apk": entry.get("apk"),
        "data_dir": entry.get("data_dir"),
        "build": entry.get("build"),
        "analysis": entry.get("analysis"),
        "recipe": entry.get("recipe"),
    }


def command_recipe_set(args) -> dict:
    from frameport import pipeline
    from frameport.core import library

    entry = library.game(args.package)
    if not entry:
        raise RuntimeError(f"package not in the library: {args.package}")
    recipe = library.recipe_from_dict(entry["recipe"])
    for assignment in args.set:
        key, _, value = assignment.partition("=")
        if not key or not _:
            raise RuntimeError(f"expected key=value, got {assignment!r}")
        try:
            parsed: float | int = float(value) if "." in value else int(value)
        except ValueError:
            parsed = value  # adapter settings may be strings
        recipe.patches[f"adapter.{key}"] = {"value": parsed}
    recipe.source = "user"
    pipeline.set_recipe(args.package, recipe)
    return {"ok": True, "package": args.package, "patches": sorted(recipe.patches)}


def _load_frame_convert():
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent / "frame-convert.py"
    spec = importlib.util.spec_from_file_location("frame_convert", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command_collect(args) -> dict:
    """Copy a built APK and its game data into this app's conversion layout."""
    import shutil

    from frameport.core import library

    source = pathlib.Path(args.source).resolve()
    if source.is_file():
        source = source.parent
    output = pathlib.Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    built = pathlib.Path(args.apk).resolve()
    if not built.is_file():
        raise RuntimeError(f"built APK does not exist: {built}")

    helper = _load_frame_convert()
    game_apk = output / f"{args.package}.apk"
    shutil.copy2(built, game_apk)
    obb_count = helper.copy_obbs(source, output, args.package)

    version_code = 0
    version_name = ""
    label = ""
    try:
        from pyaxmlparser import APK

        apk = APK(str(game_apk))
        version_code = int(apk.version_code or 0)
        version_name = str(apk.version_name or "")
        label = str(apk.application or "")
    except Exception:
        entry = library.game(args.package) or {}
        analysis = entry.get("analysis") or {}
        version_name = str(analysis.get("version") or "")
        label = str(analysis.get("label") or "")

    result = {
        "ok": True,
        "converted": True,
        "alreadyConverted": False,
        "package": args.package,
        "versionCode": version_code,
        "versionName": version_name,
        "applicationLabel": label,
        "gameApk": str(game_apk),
        "directory": str(output),
        "obbCount": obb_count,
        "sha256": helper.sha256(game_apk),
        "frameportCommit": "72c71fbbd303886992ab62cbc1aea6287100f935",
    }
    (output / "frame-conversion.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="frameport-engine.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "tools-status", "tools-install"):
        sub.add_parser(name)
    scan = sub.add_parser("scan")
    scan.add_argument("path")
    build = sub.add_parser("build")
    build.add_argument("package")
    build.add_argument("--outdir")
    show = sub.add_parser("show")
    show.add_argument("package")
    recipe_set = sub.add_parser("recipe-set")
    recipe_set.add_argument("package")
    recipe_set.add_argument("--set", action="append", default=[])
    collect = sub.add_parser("collect")
    collect.add_argument("package")
    collect.add_argument("--source", required=True)
    collect.add_argument("--output", required=True)
    collect.add_argument("--apk", required=True)
    args = parser.parse_args()

    handlers = {
        "probe": command_probe,
        "tools-status": command_tools_status,
        "tools-install": command_tools_install,
        "scan": command_scan,
        "build": command_build,
        "show": command_show,
        "recipe-set": command_recipe_set,
        "collect": command_collect,
    }

    try:
        add_engine_path()
        with contextlib.redirect_stdout(sys.stderr):
            result = handlers[args.command](args)
    except Exception as error:  # one line for the app, traceback when asked
        message = str(error) or error.__class__.__name__
        emit({"type": "error", "message": message})
        if __import__("os").environ.get("FRAMEPORT_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        return 1

    emit({"type": "result", **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
