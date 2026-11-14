# Verification

This page records what was actually exercised for Frame CyberDeck 0.2.0 and
what was not. Evidence was collected on 2026-11-14 from a clean
release-candidate tree. The final tag records the exact commit; if the tree
changes after this evidence, re-run the relevant checks.

## Validated host

- **Linux x64** — packaged build.
- **One Steam Frame** — engine-path conversion, deploy, launch, uninstall, and
  room capture.

Windows and macOS packages were built by CI but were not validated in this
cycle.

## Evidence matrix

| Area | Method | Result | Notes |
| --- | --- | --- | --- |
| Typecheck | `npm run typecheck` | Pass | Main and renderer. |
| Unit tests (Vitest) | `npm test` | 142 passed | Release-candidate run. |
| Node tests | `npm run test:node` | 71 passed, 1 skipped | Existing macOS-only skip. |
| Python tests | `npm run test:python` | 46 passed | Converter and Devkit bridge suites. |
| Lint | `npx eslint --no-cache .` | Pass | No errors. |
| FramePort vendor tree | `npm run check:frameport` | Pass | Manifest and pin verified. |
| Linux x64 package | Locally packaged AppImage/`.deb` with release-build env; CI draft still required | Pass | Linux x64 is the validated host. |
| FramePort prepare from scratch | Managed venv and tools installed through the packaged app's Settings IPC | Pass | Python 3.14 test host; Java/OVRPort/apksigner reported installed. |
| Engine-path conversion | Packaged engine resources built Singularity; Devkit bridge deployed and launched it; install removed | Pass | The app's install button was not exercised because the Frame was not exposed as the app's Steam Frame ADB target in this cycle. |
| Blade & Sorcery engine build | FramePort build plus 13 checks | 13/13 | Recorded as the engine build check. |
| Singularity engine build | FramePort build after stripped-ELF parsing fix | Installed and launched | Fix included in 0.2.0. |
| Room capture | Start, status, stop through the on-Frame agent | Pass | Capture works; no per-game provisioning. |
| Short-lived catalog key | Mint request plus mirror probe | HTTP 200 mint, HTTP 206 mirror | No credential values recorded. |
| GitHub release assets | CI matrix build | Built | Windows/macOS unverified. |

## What was not verified

- Windows x64 installation, conversion, Devkit deployment, launch, or Venture.
- macOS x64/arm64 installation, Gatekeeper flow, conversion, Devkit deployment,
  launch, or Venture.
- Linux arm64 execution.
- FramePort install-stage hardware video decode or device-side settings; these
  are not applied by Frame CyberDeck.
- Per-game room provisioning; captured rooms are not copied into MR titles.
- Venture import/play/uninstall beyond the experimental code path; Venture is
  not part of the validated host matrix.
- Windows `ia32`; not shipped for the FramePort engine.
- Other titles, other Frame builds, other Lepton versions, and other room
  shapes.
- Live contribution uploads and Meta owned-title sync in this cycle.
- AUR package installation before the v0.2.0 release asset exists.

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
deploy, launch, uninstall, and run one room capture.

## Interpretation

A passing check means the tested path worked under the recorded conditions. It
does not establish compatibility for every title, every Frame runtime, or every
host. The limits above are authoritative for this release; see
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).
