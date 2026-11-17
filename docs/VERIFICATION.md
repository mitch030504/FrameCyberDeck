# Verification

This page records what was actually exercised for Frame CyberDeck 0.3.0 and what
was not. Evidence was collected on 2026-11-17 from the release-candidate tree
after merging upstream VR CyberDeck 2.0.0 (`8bf949c`).

The 0.2.0 evidence was collected on 2026-11-14. The Frame conversion, deployment,
launch, uninstall, and room-capture code paths are unchanged in 0.3.0; they were
not re-exercised for this release, so their rows are marked as carried-over
0.2.0 evidence.

## Validated host

- **Linux x64** — 0.2.0 packaged build and a 0.3.0 production build run under
  Xvfb.
- **One Steam Frame** — 0.2.0 engine-path conversion, deploy, launch, uninstall,
  and room capture.

Windows and macOS packages were built by CI but were not validated in this
cycle. Linux arm64 was built but not executed.

## Evidence matrix

| Area | Method | Result | Notes |
| --- | --- | --- | --- |
| Typecheck | `npm run typecheck` | Pass | Main and renderer. |
| Unit tests (Vitest) | `npm test` | 183 passed | 30 files, including Venture and trailer suites. |
| Node tests | `npm run test:node` | 71 passed, 1 skipped | Existing macOS-only skip. |
| Python tests | `npm run test:python` | 46 passed | Converter and Devkit bridge suites. |
| Lint | `npx eslint --no-cache .` | Pass | 0 errors, 11 pre-existing warnings. |
| FramePort vendor tree | `npm run check:frameport` | Pass | 552 files match pin `72c71fbbd303886992ab62cbc1aea6287100f935`. |
| Production build | `npm run build` | Pass | Main, preload, and renderer bundles. |
| Venture CLI status | Real `venture-cli` 0.2.5, GUI status and library read | Pass | One test library entry; no launch or uninstall. |
| Venture version parsing | Numeric-string `versionCode` fixture and tests | Pass | 0.2.5 CLI compatibility. |
| Venture regression tests | Vitest target, setup card, and capability suites | Pass | No real device required. |
| Trailer lookup | `games:get-trailer-url` against the override registry | Pass | Returned the expected YouTube URL for a known title. |
| Trailer drawer page load | Sandboxed webview guest under Xvfb | Pass | Loaded the expected YouTube watch page. Video decode and audio were not confirmed in that environment. |
| Trailer scan | Settings scan with progress, cancel, and report | Pass | Live progress and cancel worked; report written under `userData/vrp-data`. |
| Trailer webview enforcement | Attempt to attach `example.com` and a YouTube URL | Pass | Non-YouTube attach blocked; YouTube guest attached. |
| Linux x64 package | 0.2.0 CI AppImage/`.deb` | Pass (0.2.0) | 0.3.0 packaged assets were built by CI but not executed during this evidence pass. |
| FramePort prepare from scratch | Managed venv and tools through Settings IPC | Pass (0.2.0) | Unchanged path. |
| Engine-path conversion | Packaged engine built a title; Devkit bridge deployed and launched it | Pass (0.2.0) | The app's single-click install button was not exercised. |
| Blade & Sorcery engine build | FramePort build plus 13 checks | 13/13 (0.2.0) | Carried-over engine evidence. |
| Singularity engine build | FramePort build after the stripped-ELF fix | Installed and launched (0.2.0) | Carried-over engine evidence. |
| Room capture | Start, status, stop through the on-Frame agent | Pass (0.2.0) | Capture works; no per-game provisioning. |
| Short-lived catalog key | Mint request plus mirror probe | HTTP 200 mint, HTTP 206 mirror (0.2.0) | No credential values recorded. |
| GitHub release assets | CI matrix build | Built | Windows/macOS unverified. |

## What was not verified

- Actual trailer video decode or audio on a physical host. The Xvfb test
  confirmed the guest, URL, page load, and network policy only.
- Venture import, play, or uninstall against a real installed game. The status,
  library, and GUI wiring were exercised; the launcher and installed-title
  actions were not.
- Windows x64 installation, conversion, Devkit deployment, launch, or Venture.
- macOS x64/arm64 installation, Gatekeeper flow, conversion, Devkit deployment,
  launch, or Venture.
- Linux arm64 execution.
- FramePort install-stage hardware video decode or device-side settings; these
  are not applied by Frame CyberDeck.
- Per-game room provisioning; captured rooms are not copied into MR titles.
- Other titles, other Frame builds, other Lepton versions, and other room
  shapes.
- Live contribution uploads and Meta owned-title sync in this cycle.
- AUR package installation of 0.3.0; the AUR package is updated after the GitHub
  release asset exists.

## How to reproduce

```sh
npm ci --legacy-peer-deps
npm run typecheck
npm test
npm run test:node
npm run test:python
npm run check:frameport
npx eslint --no-cache .
npm run build
```

For the packaged path, install the Linux x64 asset, prepare the FramePort
engine from Settings, connect a paired Frame, convert a compatible title,
deploy, launch, uninstall, and run one room capture. For the trailer path, open a
title with a known override, open the trailer drawer, run **Find Missing
Trailers**, cancel it, and confirm the report file is written.

## Interpretation

A passing check means the tested path worked under the recorded conditions. It
does not establish compatibility for every title, every Frame runtime, or every
host. The limits above are authoritative for this release; see
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).
