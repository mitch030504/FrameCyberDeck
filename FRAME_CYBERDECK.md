# Frame CyberDeck implementation notes

These notes describe how the 0.2.0 Frame path is implemented. They are for
contributors and maintainers; user-facing instructions live in
[docs/INSTALL.md](docs/INSTALL.md) and [docs/CONVERSION.md](docs/CONVERSION.md).

## Status and scope

- 0.2.0, first public release.
- Linux x64 is the validated host. Windows x64 and macOS x64/arm64 are built but
  unverified.
- The FramePort engine is **build-stage only** in this application. Install-stage
  hardware video decode, device-side settings, and per-game room provisioning are
  not applied.
- Room capture is implemented; per-game room provisioning is not.
- Venture is experimental.

## Device path

- Steam Frame is detected separately from Quest/Android devices through its
  SteamOS path, not by treating SteamOS as Android.
- Devkit deployment uses Valve's SteamOS Devkit infrastructure and the existing
  Devkit SSH key.
- When the Frame is attached over USB, Devkit HTTP, SSH, and rsync traffic is
  tunneled through ADB port forwards on the cable. The network path remains
  available.
- SSH records a host key on first connection and rejects changed keys later.
  Verify a reset Frame or a reused USB forwarding address before replacing the
  `known_hosts` entry.

## Conversion

### Engines

The default `auto` setting uses FramePort when its files, virtual environment,
and tools are ready. Otherwise it uses the built-in converter. `frameport`
requires preparation and reports a failure instead of silently falling back;
`auto` logs a FramePort failure and falls back for that attempt.

The built-in converter pins OVRPort CLI 1.2.5 and a prebuilt FrameBridge adapter
at the FramePort pin, verified against FramePort's SHA-256 manifest. It does not
carry FramePort's IL2CPP patch stage, which needs Cpp2IL/.NET per title.

The FramePort engine is vendored at commit
`72c71fbbd303886992ab62cbc1aea6287100f935` (v1.4.1 plus the C-Smash app-version
fix). It runs FramePort's build-stage recipes, shims, and patch sets. It prepares
its own Java, OVRPort (the latest release resolved by its toolchain), and
apksigner; it does not use the host JDK or Android SDK.

### Preparation

The engine service creates a managed environment under:

```text
<userData>/frameport-engine/
  venv/
  home/
  builds/
```

Preparation requires 64-bit Python 3.11+ with `venv` and pip, network access,
and disk space. Settings exposes engine status and a prepare/refresh action.
Missing vendored files are reported as a packaging or sync problem.

### Recipes and patches

FramePort's catalog has 291 entries; 194 actionable recipes are distilled into
`resources/frameport-recipes.json` by `scripts/distill-frameport-recipes.py`.
Entries with no adapter settings and no alternate OVRPort patch set are omitted.

For a matching title, conversion can:

- Merge recipe adapter settings into the APK's settings library. User scale and
  refresh choices win.
- Pass the recipe's alternate OVRPort patch set to the CLI.
- Inject the XR shim, Vulkan shim, OpenGL ES shader-fix layer, or GL flow where
  the recipe requires it.

The built-in path also applies the default engine patches and the recipe shims;
it does not add IL2CPP patches. A recipe is a tested configuration, not a
compatibility guarantee.

### Refresh rate

The setting offers runtime default (0), 72, 80, 90, 96, 108, 120, and 144 Hz.
`FRAME_CYBERDECK_REFRESH_RATE` overrides the saved value for development.
Unsupported values fall back to the runtime default.

Setting runtime default writes a zero refresh override into the title's recipe,
clearing an older value. The runtime must still report the requested mode as
supported.

### Output and provenance

- The source APK is never overwritten.
- Classic `*.<package>.obb` files and raw bundle folders are staged with their
  structure preserved.
- Managed installs track package, version, title, and original-source path.
- Contribution workflows use the original APK/folder and matching OBB files.
  A single-APK install without recorded OBB provenance must be reinstalled from
  the original source before contributing.

## Devkit deployment and launch

- Deployment stages the converted APK and data, then uses the Devkit bridge to
  install and register a managed title.
- New installs launch through a per-game `launch.sh` adapted from FramePort's
  launcher design (GPLv3): Steam runs the shortcut natively, the script sets the
  Lepton compatibility environment, exports `LEPTON_ENV_FRAMEBRIDGE_CONFIG` at
  the staged `settings.conf`, repairs folder permissions, takes a single-instance
  lock, and starts `lepton start`.
- If a fresh APK install is refused because the Lepton Development session is
  not running, the launcher starts `steam://rungameid/3056000` and retries once.
- On exit, the launcher clears reparented processes left by Lepton teardown so a
  leaked container init does not keep the title marked running.
- The real shortcut appid is patched into the script after registration.
- Steam binds a Devkit title's runtime at first registration and does not re-read
  it. Titles already registered as fauxdroid/Lepton APK shortcuts keep the APK
  start command; new installs use the launcher.

## On-Frame agent and room scan

The vendored agent is uploaded over Devkit SSH to:

```text
~/.local/share/frame-cyberdeck/agent/frameport_agent.py
```

The app does not replace a newer agent installed by another FramePort
installation. Only these commands are exposed:

| Group | Commands |
| --- | --- |
| Device | `info`, `battery` |
| Room | `room_status`, `room_capture`, `room_capture_status`, `room_capture_stop`, `room_put`, `room_delete`, `room_from_boundary`, `room_build`, `room_refine` |
| Settings | `set_settings` |
| Video codec | `video_codec_status`, `video_codec_switch` |

Deployment, shortcut management, and launching remain in Frame CyberDeck. The
agent's broader command set is not exposed.

Room capture starts on the Frame, reports status, and can be stopped. The app
does not receive or copy the room mesh. Captured rooms are not installed into
individual MR titles; that provisioning step is not implemented.

## Venture

An installed external `venture-cli` adds a synthetic `PC · Venture` target.
The CLI path can be set, cleared, and re-checked in Settings → Venture PC
Target; when it is missing, a setup card keeps the target discoverable. Frame
CyberDeck imports APKs, archives, and release folders through the CLI, lists
installed titles, launches them with `Play on PC`, and removes them. Import is
refused while the Venture launcher is reported open. Venture IDs are opaque
strings and may contain `:`; the library ID is used for play/uninstall and is
passed as a single argument.

Frame CyberDeck does not install Venture, sign in to Meta for it, download Meta
content for it, or convert APKs for it. Venture remains experimental and
subject to its own requirements and Meta account/terms risk.

## Meta owned-title sync

Meta sync is optional and depends on a configured server/catalog and
`upload.config`.

- Sign-in uses Meta's native SSO flow in an embedded window; the password stays
  with Meta. The resulting Quest token is validated and stored through
  `safeStorage` (OS keychain). Token storage is refused when secure storage is
  unavailable.
- One entitlement query provides each owned title's latest Android binary. No
  per-app version lookups are made.
- Updates are offered for titles the server already carries. New titles require
  a paid entitlement (PAID_OFFER, PREORDER, XBUY, SUBSCRIPTION); free downloads,
  promo keys, trials, and demos do not create new entries.
- APK and data files download from Meta's entitled CDN into
  `downloads/uploads/meta/<package>/<versionCode>/`, with a `.complete` marker,
  resumable `.part` files, ETag validation, exact-size checks, and disk-space
  preflight.
- Owned store add-ons can ride along when the rich binary query maps a data file
  to an owned pack. Lean-fallback titles with no file names get base APK + OBB
  only.
- Successful contributions are recorded so the same release is not uploaded
  twice.

## Packaging and IPC hardening

- Local thumbnails use a `local-file` protocol in the main process; the renderer
  keeps `webSecurity` enabled.
- Release notes are sanitized with DOMPurify before display.
- rclone is verified against the release's SHA256SUMS; adb platform-tools are
  verified against pinned per-platform SHA-256 values.
- Deletion resolves paths and refuses anything outside the downloads folder.
- Settings are written atomically; an unreadable file is preserved as
  `settings.json.corrupt`.
- The in-app ADB shell uses the bundled adb binary with a quote-aware tokenizer,
  not a shell.
- Device-side restore commands validate package names before interpolation.
- Release builds reject a static catalog key. Dynamic builds do not fall back to
  one after a failed mint.
- Catalog keys, 7-Zip passwords, authorization headers, token-bearing URLs, and
  credential-like fields are redacted from logs and UI errors. The build
  credential is not covered by a blanket raw-value rule; do not log it.
- The startup connectivity check does not follow redirects, so a 301 from
  `raw.githubusercontent.com` does not test the wrong host.

## Tests and CI

- `npm run typecheck` — main and renderer type checks.
- `npm test` — Vitest.
- `npm run test:node` — Node test suites.
- `npm run test:python` — converter and Devkit bridge tests.
- `npm run check:frameport` — vendored tree and manifest check.
- `npm run lint` — ESLint.
- `tests/protocol_smoke.cjs` — manual `npx electron tests/protocol_smoke.cjs`
  check for the `local-file` protocol.

The 0.2.0 evidence counts are in [docs/VERIFICATION.md](docs/VERIFICATION.md).

## Development overrides

```fish
set -x FRAME_CYBERDECK_HOST 192.168.x.x
set -x FRAME_CYBERDECK_REFRESH_RATE 90
set -x FRAME_CYBERDECK_PYTHON /path/to/python3.11
set -x VENTURE_CLI_PATH /path/to/venture-cli
npm run dev
```

| Variable | Effect |
| --- | --- |
| `FRAME_CYBERDECK_HOST` | Explicit Devkit host. |
| `FRAME_CYBERDECK_REFRESH_RATE` | Development refresh-rate override. |
| `FRAME_CYBERDECK_PYTHON` | Python interpreter for conversion/engine preparation. |
| `VENTURE_CLI_PATH` | Explicit `venture-cli` path. |
| `FRAME_CYBERDECK_CLIENT_ID` | Development client id override. |
| `FRAME_CYBERDECK_BUILD_SECRET` | Development build-credential override. |
| `FRAME_CYBERDECK_KEY_URL` | HTTPS key endpoint override in non-packaged builds. |

## Related documents

- [docs/CONVERSION.md](docs/CONVERSION.md)
- [docs/ROOM_SCAN.md](docs/ROOM_SCAN.md)
- [docs/VENTURE.md](docs/VENTURE.md)
- [docs/CATALOG.md](docs/CATALOG.md)
- [docs/security/catalog-credentials.md](docs/security/catalog-credentials.md)
- [docs/VERIFICATION.md](docs/VERIFICATION.md)
