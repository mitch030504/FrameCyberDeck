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
- Deploys converted titles through SteamOS Devkit and the `fauxdroid` runtime.
- Tracks managed package/version/title metadata.
- Preserves original-source provenance for contribution workflows.

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
