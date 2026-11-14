# Changelog

All notable changes to Frame CyberDeck are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use
semantic versioning for this fork.

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
