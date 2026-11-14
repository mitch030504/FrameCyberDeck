# Vendored FramePort engine

This directory is a pinned copy of FramePort's headless build engine and device
agent, used by Frame CyberDeck to build Steam Frame conversions with the full
upstream patch pipeline.

- Upstream: https://github.com/spoopyghosty0/frameport
- Pin: `72c71fbbd303886992ab62cbc1aea6287100f935` (tag v1.4.1 plus the
  C-Smash app-version fix)
- License: GPL-3.0-only (see `LICENSE`); FramePort's third-party notices are in
  `THIRD_PARTY_NOTICES.md`

## What is included

- `src/frameport/` — the engine, without FramePort's Flet UI (`ui/`).
- `catalog/` — the bundled game recipe catalog at the pin.
- `artifacts/` — prebuilt adapter, shim, dex and codec artifacts.
- `agent/` and `bootstrap/` — the on-Frame agent and setup scripts.
- `requirements.txt` — the Python dependency set used by the engine.

`resources/frameport-engine.py` is Frame CyberDeck's JSON-lines entry point;
it adds `src/` to `sys.path` and calls the upstream engine.

## Updating

```bash
python3 scripts/sync-frameport-engine.py          # fetch the pin and rewrite this tree
python3 scripts/sync-frameport-engine.py --check  # verify MANIFEST.sha256
```

Change the `PIN` constant in the sync script first when moving to a new
upstream release. CI runs `--check`, so a manual edit here fails the build.
Do not edit vendored files by hand; put Frame CyberDeck changes in the runner
or in the calling service instead.
