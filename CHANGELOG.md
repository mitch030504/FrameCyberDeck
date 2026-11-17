# Changelog

All notable changes to Frame CyberDeck are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use
semantic versioning for this fork.

## [0.3.0] — 2026-11-17

This release completes the Venture PC target, syncs the fork to upstream VR
CyberDeck 2.0.0 (`8bf949c`), and restores trailer lookup, playback, scanning, and
reporting. The main window keeps the fork's hardened Electron settings.

### Added

- First-class Venture PC target: a synthetic `PC · Venture` device with a setup
  card, persisted CLI path, real `ventureId` propagation, and Play and uninstall
  actions from details and the context menu.
- Venture 0.2.5 CLI support, including numeric-string `versionCode` values.
- Trailer lookup and playback from game details in a collapsible drawer, with a
  report link that pre-fills the upstream issue form.
- In-app **Find Missing Trailers** scan for the whole library with live progress,
  cancel, a written report, and a maintainer review script.
- Trailer override registry: bundled overrides, background refreshes from the
  upstream data file, and candidate matching for store metadata.
- Trailer report workflow for `[Trailer]` issues, with validated package names
  and YouTube video IDs before anything is written to `data/trailer-reports.json`.
- Upstream ADB push-error diagnostics in backup restore logs.

### Changed

- Synced to upstream VR CyberDeck 2.0.0 (`8bf949c`): trailer service, metadata
  candidates, override data, scripts, and Settings UI.
- Backups now reject all-empty captures and force-stop the game before a restore.
- Venture import wording, launcher warnings, and hidden ADB/OBB controls now
  reflect a PC target rather than a headset.
- The release package remains `frame-cyberdeck` on the fork's `0.x` line;
  upstream package names and versions are not adopted.

### Fixed

- Venture IDs that contain `:` import and play correctly.
- Venture CLI 0.2.5 string `versionCode` values no longer break library parsing.
- Trailer scan failures now surface in Settings instead of failing silently.
- Restored trailer playback no longer raises unhandled rejections when the guest
  page is not ready or navigates.
- `.gitignore` now separates `*.tsbuildinfo` and the trailer review ignore file;
  generated `tsconfig.node.tsbuildinfo` is not tracked.

### Security

- Trailer playback runs in a dedicated YouTube session as a sandboxed webview:
  no Node, no preload, context isolation on, and web security on.
- Guest attach and navigation are restricted to YouTube-owned HTTPS hosts, and
  popups are denied. The parent window remains `sandbox: true` with context
  isolation and no Node integration.
- Renderer `frame-src` allows only the YouTube hosts required for trailers.
- Upstream's `sandbox: false` and `webSecurity: false` were deliberately not
  adopted.
- Trailer reports are parsed, length-limited, and stored as JSON; package names
  and YouTube IDs are validated before a report is accepted.

### Upgrade

- From 0.2.0: settings, downloads, backups, and converted titles carry over. No
  migration is required. Set the Venture CLI path in Settings or
  `VENTURE_CLI_PATH` to enable the PC target.
- From 0.1.0 or upstream 1.9.x: see the 0.2.0 notes; this remains a separate
  application and version line.

### Verified

- 183 Vitest tests, 71 Node tests (1 skipped), and 46 Python tests; typecheck,
  lint, `check:frameport`, and the production build pass.
- Venture GUI against a real 0.2.5 CLI and library: status, library listing,
  import wording, and Play wiring. A real launch or uninstall was not performed.
- Trailer lookup, drawer page load, scan/progress/cancel/report, and webview host
  enforcement under Xvfb. Actual video decode was not confirmed in that
  environment.
- Linux x64 remains the validated host from 0.2.0. Windows and macOS packages
  are built but unverified. See [docs/VERIFICATION.md](docs/VERIFICATION.md).

### Known limitations

- Trailer playback needs network access. A fresh YouTube session may show
  Google's consent screen once; the choice is stored in the dedicated session.
- Venture remains experimental and requires an external CLI. Close the Venture
  launcher before importing.
- Windows and macOS packages remain experimental and unverified.

See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the evidence matrix and
what was not tested.

## [0.2.0] — 2026-11-14

The first public release. 0.1.0 was a development/AUR build and was never
published on GitHub.

### Added

- Vendored FramePort 1.4.1 headless engine at pin
  `72c71fbbd303886992ab62cbc1aea6287100f935`, with 194 actionable recipes
  distilled from 291 catalog entries.
- Conversion engine setting: `auto` (default), `frameport`, and `builtin`.
- FramePort engine preparation and status UI, including a managed Python 3.11+
  environment and downloads for its Java, OVRPort, and apksigner tools.
- FramePort on-Frame agent deployment over the existing SteamOS Devkit SSH
  connection, with an explicit command allowlist.
- Mixed Reality Room scan panel: start, status, and stop.
- Venture PC target for an external `venture-cli`: synthetic `PC · Venture`
  device, import, list, play, and uninstall.
- Short-lived catalog key exchange for release builds: public client id plus
  obfuscated build credential, random installation id, app version, in-memory
  grant, refresh, and explicit 403/503 handling.
- `SHA256SUMS` publication for release assets.
- Documentation set for install, conversion, room scan, Venture, catalog
  access, limitations, verification, releases, and catalog credentials.

### Changed

- `auto` is the documented default. FramePort is used only when its files,
  virtual environment, and tools are ready; otherwise the built-in converter is
  used.
- The built-in fallback was refreshed to OVRPort CLI 1.2.5 and the 194-recipe
  catalog.
- FramePort recipes merge adapter settings into the converted APK, select
  alternate OVRPort patch sets, and add the matching shims. User scale and
  refresh choices still win.
- New installs use a per-game `launch.sh` with the Lepton environment,
  single-instance handling, permission repair, and shortcut appid patching.
- USB Devkit traffic is tunneled through ADB port forwards; network Devkit
  access still works.
- Data staging handles classic OBB layouts and raw bundle folders.
- Managed installs retain original-source provenance for contributions.

### Fixed

- FramePort agent bridge invocation and stripped-ELF section parsing.
- Catalog key retry when a mirror rejects a freshly minted key.
- `Runtime default` refresh rate now clears a previous recipe override instead
  of leaving the old rate in place.
- Venture IDs that contain `:` are handled as opaque values, and manual import
  no longer breaks on them.
- 7-Zip passwords are redacted from logs, UI errors, and process output.
- Catalog keys and token-bearing URLs are redacted from logs.

### Security

- Release builds require the short-lived key exchange and fail if a static
  `VRSRC_API_KEY` is configured.
- Dynamic builds never fall back to a static catalog key after a failed mint.
- The minted key is sent only to the HTTPS `baseUri` returned by the grant, not
  to an arbitrary configured base URI.
- The embedded build credential is obfuscated, not confidential. It is
  extractable from a shipped build and can be revoked server-side.
- Support logs go to a public rentry.co paste. Catalog keys, 7-Zip passwords,
  and authorization headers are redacted, but logs may still contain command
  output; users must review logs before sharing them.

### Upgrade

- From 0.1.0 (development/AUR): settings and downloads carry over. Existing
  converted titles are not rebuilt; redeploy from the original source to pick
  up the new launcher.
- From upstream VR CyberDeck 1.9.x: this is a separate application and version
  line. It installs side by side and does not migrate settings or library state.
- Run **Prepare FramePort engine** before the first FramePort conversion. Until
  then `auto` uses the built-in converter.

### Known limitations

- Linux x64 is the validated host. Windows x64 and macOS x64/arm64 are built but
  unverified; Windows `ia32` is not supported by the FramePort engine.
- The FramePort engine applies build-stage patches only. Install-stage hardware
  video decode, device-side settings, and per-game room provisioning are not
  applied by Frame CyberDeck.
- Room capture works; captured rooms are not copied into individual MR titles.
- Venture support is experimental and requires an external CLI.
- Compatibility is per title and per build. A recipe is a configuration, not a
  guarantee.
- The build credential is extractable; catalog access is optional.
- Windows builds are unsigned. macOS builds are ad-hoc signed and not
  notarized.

### Verified

- Linux x64 packaged build.
- One Frame engine-path conversion using packaged engine resources; Devkit
  bridge deploy, launch, and uninstall. The app's single-click install button
  was not exercised in this cycle.
- FramePort engine preparation from scratch.
- Blade & Sorcery engine build: 13/13 checks.
- Singularity engine build after the stripped-ELF fix: installed and launched.
- Room capture, status, and stop.
- Short-lived key mint HTTP 200; mirror probe HTTP 206.
- 142 Vitest tests, 71 Node tests (1 skipped), 46 Python tests; typecheck and
  lint green on the release candidate.

See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the evidence matrix and
what was not tested.

### Credits

Frame CyberDeck is GPL-3.0-only. It is an independent fork of VR CyberDeck by
DeliciousMeatPop, built on ApprenticeVR by jimzrt. Vendored FramePort 1.4.1 is
by spoopyghosty0 and is GPL-3.0-only. OVRPort, AXRB, RiftLift, and optional
Venture are credited in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## [0.1.0] — development build

- Initial AUR package and development builds. Never published on GitHub.
