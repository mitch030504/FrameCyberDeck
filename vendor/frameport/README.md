# Vendored FramePort engine

This directory is a pinned copy of FramePort's headless build engine and
on-Frame agent, used by Frame CyberDeck for Steam Frame conversion.

## Pin and provenance

| Item | Value |
| --- | --- |
| Upstream | https://github.com/spoopyghosty0/frameport |
| Pin | `72c71fbbd303886992ab62cbc1aea6287100f935` |
| Upstream tag | v1.4.1 plus the C-Smash app-version fix |
| License | GPL-3.0-only (see `LICENSE`) |
| Third-party notices | `THIRD_PARTY_NOTICES.md` |

`PIN.json` records the machine-readable pin. `MANIFEST.sha256` covers the
vendored tree and is checked by CI.

## Layout

- `src/frameport/` — the headless engine, without FramePort's Flet UI (`ui/`).
- `catalog/` — the catalog at the pin: **291 entries**, of which **194** are
  actionable recipes after distillation.
- `artifacts/` — prebuilt target libraries, shims, dex files, codec support,
  room-scan binaries, and host artifacts under `arm64-v8a/`, `armeabi-v7a/`,
  `dex/`, `hevc/`, `linux-arm64/`, `linux-arm64-bin/`, `roomscan/`, and
  `win-x64/`.
- `agent/` — the on-Frame agent.
- `bootstrap/` — FramePort setup scripts.
- `requirements.txt` — the pinned Python dependency set.
- `SHA256SUMS` — artifact manifest inside `artifacts/`.

`resources/frameport-engine.py` is Frame CyberDeck's JSON-lines entry point. It
adds `src/` to `sys.path` and calls the upstream engine.

## How Frame CyberDeck runs it

The app uses the engine's **build-stage** pipeline. It does not apply FramePort's
install-stage hardware video decode, device-side settings, or per-game room
provisioning.

Preparation:

1. Creates `<userData>/frameport-engine/venv` with 64-bit Python 3.11+.
2. Installs `requirements.txt`.
3. Downloads FramePort's Java, OVRPort, and apksigner tools.
4. Reports tool status in Settings.

The first prepare needs network access. Later builds reuse the managed
environment and tools unless they are removed or invalidated. The engine's
scratch output lives under `<userData>/frameport-engine/builds`.

The built-in fallback is separate: it uses OVRPort CLI 1.2.5 and a prebuilt
FrameBridge adapter at the same pin, verified against the SHA-256 manifest.

## Updating the pin

Change the `PIN` constant in `scripts/sync-frameport-engine.py` before moving to
a new upstream release, then:

```bash
python3 scripts/sync-frameport-engine.py          # fetch the pin and rewrite this tree
python3 scripts/sync-frameport-engine.py --check  # verify MANIFEST.sha256
```

If the catalog changed, regenerate the distilled recipes:

```bash
python3 scripts/distill-frameport-recipes.py /path/to/frameport
```

CI runs `--check`, so a manual edit or stale tree fails the build. Keep local
patches small and documented; do not edit vendored files by hand. Put Frame
CyberDeck behavior in the runner or the calling service instead.

## Licensing and source

FramePort is GPL-3.0-only. This repository includes the pinned source,
`LICENSE`, `THIRD_PARTY_NOTICES.md`, and the artifact manifest. Redistributors
must preserve those notices and provide the corresponding source as required by
the GPL. The vendored tree is the source for the engine and agent used by this
application; the sync script records how it was produced.

The Flet UI is not included. The local engine integration and the stripped-ELF
reader tolerance are small modifications documented in `THIRD_PARTY_NOTICES.md`
and the sync script's `VENDOR_PATCHES`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| “Vendored FramePort engine files are missing” | The packaged resources are incomplete; reinstall, or run the sync script in a source tree. |
| Preparation fails | Python is older than 3.11, `venv`/pip is unavailable, network access is blocked, or disk space is low. |
| Tools remain missing | Use **Refresh engine status**, then **Prepare FramePort engine** again. Check the app log for the failing tool. |
| Engine build fails | Try `auto` or the built-in converter to distinguish engine failure from title incompatibility. |
