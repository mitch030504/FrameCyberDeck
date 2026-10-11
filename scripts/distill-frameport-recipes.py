#!/usr/bin/env python3
"""Distill FramePort's game catalog into resources/frameport-recipes.json.

Usage:
    python3 scripts/distill-frameport-recipes.py /path/to/frameport

The frameport checkout should be at (or newer than) the commit pinned in
resources/frame-convert.py (FRAMEPORT_COMMIT). Requires PyYAML on the
machine that runs this script (the generated JSON has no runtime deps).

Recipes with neither adapter settings nor an alternate OVRPort patch set
are dropped: there is nothing for the converter to act on.

Catalog (c) spoopyghosty0, https://github.com/spoopyghosty0/frameport — GPLv3.
"""
import json
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAMEPORT_COMMIT = "5424e429e46e53fe51497babac124dd91dbb73bf"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    catalog_dir = pathlib.Path(sys.argv[1]).resolve() / "catalog" / "games"
    if not catalog_dir.is_dir():
        raise SystemExit(f"No catalog at {catalog_dir}")

    recipes: dict[str, dict] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        package = data.get("package")
        adapter = {k: v for k, v in (data.get("adapter") or {}).items() if isinstance(v, (str, int, float))}
        alt = [str(p) for p in (data.get("alt_overport") or [])]
        use_alt = bool(data.get("use_alt"))
        if not adapter and not (use_alt and alt):
            continue
        entry: dict = {"title": str(data.get("title") or package)}
        if data.get("status"):
            entry["status"] = str(data["status"])
        if adapter:
            entry["adapter"] = {str(k): v for k, v in adapter.items()}
        if use_alt and alt:
            entry["alt_overport"] = alt
        recipes[str(package)] = entry

    out = {
        "_source": (
            "Game recipes from FramePort by spoopyghosty0 "
            "(https://github.com/spoopyghosty0/frameport), "
            f"catalog commit {FRAMEPORT_COMMIT}. GPLv3, like this project."
        ),
        "recipes": recipes,
    }
    destination = REPO_ROOT / "resources" / "frameport-recipes.json"
    destination.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(recipes)} recipes to {destination}")


if __name__ == "__main__":
    main()
