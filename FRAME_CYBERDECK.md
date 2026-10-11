# Frame CyberDeck implementation notes

Frame CyberDeck is the Steam Frame-focused fork of VR CyberDeck.

## Current Frame path

- Detects native SteamOS ADB separately from Quest/Android devices.
- Converts compatible Quest APKs before deployment.
- Skips re-downloading a release that is already complete on disk, verified file-by-file against its own `release.manifest`.
- Uses OVR Port `3.4.3-23204ea`.
- Injects the prebuilt FrameBridge adapter from FramePort, pinned to commit `5424e429e46e53fe51497babac124dd91dbb73bf` and SHA-256-verified against FramePort's manifest. Its passthrough emulation and swapchain fixes are built in; no separate compatibility wrapper is needed.
- Invalidates cached adapters that do not match the pinned SHA-256.
- Supports optional per-game refresh-rate requests: runtime default, 72, 80, 90, 96, 108, 120 or 144 Hz.
- Deploys converted titles through SteamOS Devkit. When the Frame is attached over USB, devkit traffic (HTTP, SSH, rsync) is tunneled through ADB port forwards on the cable instead of Wi-Fi.
- New installs launch through a per-game `launch.sh` (launcher design by spoopyghosty0/FramePort, GPLv3, adapted to this app's layout): Steam runs the shortcut natively (no compat tool), the script sets the Lepton compat env, exports `LEPTON_ENV_FRAMEBRIDGE_CONFIG` at the staged `settings.conf` (live adapter settings), repairs folder permissions, holds a single-instance lock, and calls `lepton start` itself. The real shortcut appid is patched into the script after registration. Steam binds a devkit game's runtime at its FIRST registration and never re-reads it, so titles that were already registered as fauxdroid/Lepton APK shortcuts keep the APK start command (detected per gameid at deploy time).
- Copies release data from both Quest layouts: classic `*.<package>.obb` files and raw bundle files inside a package-named folder; staging preserves the folder structure, so raw-bundle games (e.g. Unity asset-bundle releases) install with their full data.
- Applies FramePort per-game recipes and default engine patches (by spoopyghosty0, https://github.com/spoopyghosty0/frameport, GPLv3). Recipe adapter settings merge into `libframe_settings.so` (the user's scale/refresh choices win); alternate OVRPort patch sets go to the CLI's `--patches`. Default patches on every build: the manifest's category INFO retargeted to LAUNCHER (Lepton only launches LAUNCHER; binary AXML edit), the OVRPort dispatcher's swapchain guard raised from 4096 to 16384 px, the Vulkan shim (`libfp_vk.so`) for every engine that loads Vulkan by name, the Meta XR Audio telemetry FindClass call neutralized (word-level ARM64 pattern, no disassembler), missing Meta platform functions covered by `libovrplatformcompat.so` plus generated no-op stand-ins (pure-Python ELF synthesis, no compiler), Meta's avatar loader stubbed out (no Horizon avatar driver on the Frame), and the LTW depth shim for Minecraft launchers. Recipe shims: the XR shim (`libframe_xrshim.so`, OVRPlugin `dlopen` rewrite), the GLES shader-fix layer (first DT_NEEDED of the engine library), and the GL flow (`libvrapi.so` replaced with the VrApi→OpenXR bridge plus `libglshim.so` ahead of libEGL). BlazeRush's exact build gets its two controller/menu instruction edits and shader-cache namespace change, fingerprint-checked. Recipes are distilled into `resources/frameport-recipes.json` by `scripts/distill-frameport-recipes.py`; artifacts download from the pinned FramePort commit with SHA-256 verification. Undeclared Meta permissions (`com.oculus.permission.*` / `horizonos.permission.*` the app uses) are declared in the manifest so they are granted at install (mixed-reality 'use spatial data' checks, for example Demeter). Not carried over from FramePort: il2CPP patches (they need Cpp2IL/.NET per game) and hardware video decode and device-side settings (they need FramePort's on-Frame agent).
- Tracks managed package/version/title metadata.
- Preserves original-source provenance for contribution workflows.

## Meta owned-title sync

- The Meta sign-in window captures the `oc_ac_at` session cookie after a normal browser login (2FA included); the password never reaches the application. A user-token paste is the fallback. Tokens are stored via `safeStorage` (OS keychain) and never logged.
- A single entitlement query provides each owned title's latest Android binary (binary id, version code, package name) directly on the entitlement item; no per-app version lookups are made, which also keeps the check well inside Meta's rate limits.
- Candidates: updates for any title the server already carries, and new titles only when the entitlement was paid (PAID_OFFER, PREORDER, XBUY, SUBSCRIPTION — Horizon+ claimed titles included; a grant reason containing `SUBSCRIPTION` counts). Free downloads (NUX, UNKNOWN, promo keys, trials) never start a new upload.
- APK and OBB files download from Meta's entitled CDN into `downloads/uploads/meta/<package>/<versionCode>/`, marked with a `.complete` marker so re-checks skip them, and are enqueued through the existing local upload pipeline (HWID + archive + `upload.config` rclone) unchanged.
- All Meta responses are read defensively; a shape drift surfaces as a clear "Meta returned no …" error instead of a crash.

## Packaging and IPC hardening

- Local thumbnails load through a `local-file` protocol registered in the main process; the renderer keeps the same-origin policy enabled (`webSecurity` stays on).
- Update release notes are markdown-rendered and sanitized with DOMPurify before display.
- Downloaded dependencies are verified before use: rclone against the release's SHA256SUMS manifest, adb platform-tools against a pinned per-platform SHA-256.
- Deleting downloaded game files resolves the target path and refuses anything outside the downloads folder.
- Settings are written atomically (temp file + rename); an unreadable settings file is preserved as `settings.json.corrupt` instead of being overwritten.
- The in-app ADB shell runs the bundled adb binary directly (`execFile`) with a quote-aware argument tokenizer — no shell in the path.
- Device-side restore commands validate the package name against Android's character set before interpolating it into `run-as` commands.
- The startup connectivity check does not follow redirects: `raw.githubusercontent.com` answers with a 301 to `github.com`, and following it would test the wrong host (and fail on networks that can only reach GitHub over IPv6).

## Tests

- `npm run test:python` — unit tests for the converter's pure logic (`tests/frame_convert_test.py`).
- `tests/protocol_smoke.cjs` — run manually with `npx electron tests/protocol_smoke.cjs` to verify the `local-file` protocol serves images with default web security.
- Both suites run in CI alongside the existing typecheck, vitest and node tests.

## Development overrides

```fish
set -x FRAME_CYBERDECK_HOST 192.168.x.x
set -x FRAME_CYBERDECK_REFRESH_RATE 90
npm run dev
```

The refresh-rate override is optional. Without it, FrameCyberDeck keeps the runtime-selected refresh rate.
