<p align="center">
  <img src="build/icon.png" width="160" alt="Frame CyberDeck">
</p>

# Frame CyberDeck

**Steam Frame-focused fork of VR CyberDeck**

Frame CyberDeck is an independent, GPL-3.0-only desktop client for Valve's Steam Frame. It converts compatible Quest APKs using a vendored FramePort engine or a built-in fallback, deploys them through SteamOS Devkit, launches and uninstalls managed titles, captures mixed-reality rooms with FramePort's on-Frame agent, and can target an optional Venture PC library.

It is not affiliated with or endorsed by Valve, Meta, FramePort, Venture, or the VR CyberDeck maintainer.

> `> FRAME DETECTED. CONVERT. DEPLOY. LAUNCH.`

```text
[ TARGET ] STEAM FRAME
[ HOST   ] LINUX · WINDOWS · macOS
[ STACK  ] ELECTRON · REACT · TYPESCRIPT
[ DEPLOY ] STEAMOS DEVKIT · LEPTON
```

## Status

| Item | Value |
| --- | --- |
| Release | **0.2.0**, first public release (2026-11-14) |
| Upstream base | VR CyberDeck 1.9.0 |
| Conversion engine | Vendored FramePort 1.4.1, pin `72c71fbbd303886992ab62cbc1aea6287100f935` |
| Validated host | **Linux x64** |
| Other packages | Windows x64 and macOS x64/arm64 are built but **experimental and unverified** |
| License | GPL-3.0-only |

0.1.0 was a development/AUR build and was never published on GitHub. See [CHANGELOG.md](CHANGELOG.md) for the release history.

## Quick start

1. Download the Linux x64 AppImage or `.deb` from the release, verify it against `SHA256SUMS`, and install it. Windows and macOS packages are experimental; see [docs/INSTALL.md](docs/INSTALL.md).
2. On the Frame, install Lepton and pair SteamOS Devkit access with Valve's SteamOS Devkit Client.
3. Connect the Frame by USB or Wi-Fi and start Frame CyberDeck.
4. Optional but recommended for the FramePort engine: open **Other Settings → Steam Frame Conversion → Prepare FramePort engine**. The app creates a managed Python environment and downloads its conversion tools once.
5. Drop a compatible Quest APK, ZIP, or game folder onto the deck, choose the target, install, then launch.

Local sideloading, conversion, deployment, launch, uninstall, and room capture do not require catalog credentials.

## Features and maturity

| Area | Status | Notes |
| --- | --- | --- |
| Steam Frame detection and Devkit deployment | **Validated on Linux x64** | Devkit deploy, launch, and uninstall on one Frame path; the app's single-click install button was not exercised. |
| Quest-to-Frame conversion | **Validated with one engine-path title** | `auto` is the default. FramePort is used only after it is prepared; the built-in converter is the fallback. Compatibility is per title. |
| FramePort recipes and shims | **Build-stage only** | 194 actionable recipes from 291 catalog entries. Recipes are configurations, not a guarantee. |
| Mixed-reality room capture | **Validated** | Capture, status, and stop work. Captured rooms are not copied into MR titles. |
| Venture PC target | **Experimental** | Requires an external `venture-cli`. |
| Meta owned-title sync and contributions | **Optional** | Requires a configured server and `upload.config`; not required for local use. |
| Original-source provenance | **Implemented** | Managed installs record the original source; contribution packaging is local-only. |
| Catalog downloads | **Optional** | Release builds use a short-lived key. Local mode works without it. |

No "download all" or bulk-download function is provided. Catalog downloads are capped at two concurrent titles.

## Requirements

### Conversion engines

| Engine | Requirements |
| --- | --- |
| **FramePort** (recommended after preparation) | 64-bit Python 3.11+ with `venv` and pip; network access for the first prepare; free disk space for the managed environment and tools. No host JDK or Android SDK is required. |
| **Built-in fallback** | Python 3, JDK 21+, and Android SDK build-tools containing `aapt`, `zipalign`, and `apksigner`. Set `ANDROID_SDK_ROOT` and `JAVA_HOME` if they are not in standard locations. |

The `auto` setting uses FramePort when its files, virtual environment, and tools are ready. Otherwise it uses the built-in converter. Selecting `frameport` explicitly fails rather than silently falling back.

### Frame and host

- SteamOS Devkit pairing and the Devkit SSH key at `~/.config/steamos-devkit/devkit_rsa`.
- `ssh` on the host; `rsync` on Linux/macOS for Devkit transfers. Windows uses a tar stream over SSH.
- Lepton installed on the Frame. Frame CyberDeck launches through Lepton and can start the Lepton Development session (`steam://rungameid/3056000`) when an install is refused.
- OS key storage if you use Meta sign-in. On Linux this normally means a working Secret Service/keyring.
- Windows `ia32` is not supported by the FramePort engine.

### Host validation

- **Linux x64:** validated; use the AppImage or `.deb`.
- **Linux arm64:** built by CI, not validated in this release.
- **Windows x64:** built by CI, experimental and unverified; unsigned.
- **macOS x64/arm64:** built by CI, experimental and unverified; ad-hoc signed, not notarized; macOS 12 or newer.

## Install, upgrade, and uninstall

Release assets are listed in [docs/INSTALL.md](docs/INSTALL.md). Verify downloads against the attached `SHA256SUMS`.

Upgrading from the 0.1.0 AUR build preserves settings and downloads, but existing converted titles are not rebuilt. Redeploy from the original source to pick up the new per-game launcher. Upstream VR CyberDeck 1.9.x is a separate application and version line; it installs side by side and does not migrate settings or library state.

Default data lives in the Electron `userData` folder:

| Platform | Default `userData` |
| --- | --- |
| Linux | `~/.config/frame-cyberdeck` |
| macOS | `~/Library/Application Support/frame-cyberdeck` |
| Windows | `%APPDATA%\frame-cyberdeck` |

Downloads default to `<userData>/downloads`; uploads, save backups, logs, the FramePort environment, and the catalog installation id live under the same root. Removing `userData` removes settings, downloads, provenance, logs, and the managed engine. See [docs/INSTALL.md](docs/INSTALL.md) for the full list and uninstall steps.

## Steam Frame setup

1. Pair the Frame once with Valve's SteamOS Devkit Client. Frame CyberDeck reuses the normal Devkit SSH key.
2. Connect by USB for the cable path, or use the normal Devkit network path. USB Devkit traffic (HTTP, SSH, rsync) is tunneled through ADB port forwards.
3. If discovery does not find the Frame during development, set the host explicitly:

```fish
set -x FRAME_CYBERDECK_HOST 192.168.x.x
npm run dev
```

SSH records a host key on first connection and rejects changed keys. If a Frame is reset, or another Frame uses the same USB forwarding address, verify the device before removing the old `known_hosts` entry.

## Conversion

Open **Other Settings → Steam Frame Conversion** to choose the engine and refresh rate, prepare or refresh the FramePort engine, and view tool status.

- **Automatic (default):** FramePort when prepared, otherwise the built-in converter.
- **FramePort engine:** build-stage recipes, shims, and patches. Requires preparation; does not fall back automatically.
- **Built-in converter:** pinned FrameBridge adapter and OVRPort 1.2.5. It does not carry FramePort's IL2CPP patches.
- **Refresh rate:** Runtime default (0), 72, 80, 90, 96, 108, 120, or 144 Hz. Runtime default also clears a previous recipe override. The runtime must report the requested mode as supported.

The FramePort path applies build-stage changes only. FramePort's install-stage hardware video decode, device-side settings, and per-game room provisioning are **not** applied by Frame CyberDeck. See [docs/CONVERSION.md](docs/CONVERSION.md).

The original source APK is never overwritten. Managed installs record which original Quest payload produced the Frame build so contribution workflows can use the original APK/folder and its OBB data.

## Room scanning

With a Frame connected, open **Other Settings → Steam Frame Conversion → Mixed Reality Room** and choose **Scan room on Frame**. The scan runs on the Frame through FramePort's agent (about 90 seconds); Frame CyberDeck only starts it and reports status. **Cancel scan** stops an active capture.

Captured rooms stay on the Frame. They are not copied into individual MR games in this release. See [docs/ROOM_SCAN.md](docs/ROOM_SCAN.md).

## Venture PC target

When an external `venture-cli` is installed, Frame CyberDeck adds a synthetic **PC · Venture** target to the device list. The normal install flow can import a release folder into Venture; installed Venture titles can be listed, launched, and uninstalled from the deck.

Venture support is **experimental**. Frame CyberDeck does not install Venture, sign in to Meta for it, or download Meta content for it. Close the Venture launcher before importing. Using Venture may carry Meta account/terms risk; see [docs/VENTURE.md](docs/VENTURE.md).

## Catalog, privacy, and credentials

Catalog access is optional. Local sideloading and Frame management work without it.

Release builds carry a public client id and an obfuscated build credential. At runtime the app exchanges them for a short-lived key at `https://vrsrc.fyi/api/mirror/app-key`, sends a random installation id and the app version, caches the key in memory until `refreshAfter`, refreshes it after a 403, and waits 60 seconds once if the server answers 503 `key_pending`. The minted key is used only with the HTTPS `baseUri` returned in the grant.

The build credential is obfuscated, not confidential: it can be recovered from a shipped build. Revocation is server-side. Release builds reject a static `VRSRC_API_KEY` and never fall back to one after a failed mint.

The app also checks GitHub for updates. Support logs uploaded from Settings go to a **public** rentry.co paste; catalog keys, 7-Zip passwords, and authorization headers are redacted, but logs may still contain command output. Review the log before sharing.

See [docs/CATALOG.md](docs/CATALOG.md) and [docs/security/catalog-credentials.md](docs/security/catalog-credentials.md).

## Troubleshooting, limitations, and verification

- [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) — platform, engine, room, Venture, catalog, and packaging limits.
- [docs/VERIFICATION.md](docs/VERIFICATION.md) — what was tested for 0.2.0, with dates and evidence.
- Troubleshooting is covered in [docs/INSTALL.md](docs/INSTALL.md) and [docs/CONVERSION.md](docs/CONVERSION.md).

## Build from source

Use Node.js 22.18 or newer and npm. `package-lock.json` is authoritative.

```sh
npm ci --legacy-peer-deps
npm run typecheck
npm test
npm run test:node
npm run test:python
npm run check:frameport
npm run build
```

Build an installable package:

| Platform | Command |
| --- | --- |
| Linux x64 | `npm run build:linux:x64` |
| Linux ARM64 | `npm run build:linux:arm64` |
| Windows x64 | `npm run build:win:x64` |
| macOS x64 | `npm run build:mac:x64` |
| macOS ARM64 | `npm run build:mac:arm64` |

Source builds do not need catalog credentials. For catalog development, copy `.env.example` to `.env` and set `VRSRC_CLIENT_ID` and `VRSRC_BUILD_SECRET` (the build credential). Release builds require both and fail if a static catalog key is present. Never commit a real credential.

`python3 scripts/verify-catalog-key.py` mints a key and probes the mirror without printing either credential.

## Credits and license

Frame CyberDeck is **GPL-3.0-only**. It is an independent fork of **VR CyberDeck** by **DeliciousMeatPop**, which was built on **ApprenticeVR** by **jimzrt**. It is not an official VR CyberDeck release.

Vendored FramePort 1.4.1 is by **spoopyghosty0** and is GPL-3.0-only. OVRPort is GPL-3.0; AXRB is GPL-3.0; RiftLift is GPL-3.0-or-later; optional Venture is GPL-3.0-or-later. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [vendor/frameport/THIRD_PARTY_NOTICES.md](vendor/frameport/THIRD_PARTY_NOTICES.md), and [LICENSE](LICENSE).

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md)
- [docs/CONVERSION.md](docs/CONVERSION.md)
- [docs/ROOM_SCAN.md](docs/ROOM_SCAN.md)
- [docs/VENTURE.md](docs/VENTURE.md)
- [docs/CATALOG.md](docs/CATALOG.md)
- [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md)
- [docs/VERIFICATION.md](docs/VERIFICATION.md)
- [docs/RELEASING.md](docs/RELEASING.md)
- [docs/security/catalog-credentials.md](docs/security/catalog-credentials.md)
- [FRAME_CYBERDECK.md](FRAME_CYBERDECK.md) — implementation notes
- [AUDIT_FOLLOWUP.md](AUDIT_FOLLOWUP.md) — historical 2026-10-11 audit
- [CHANGELOG.md](CHANGELOG.md)
- [AUR/README.md](AUR/README.md)
