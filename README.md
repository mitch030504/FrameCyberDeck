<p align="center">
  <img src="build/icon.png" width="160" alt="Frame CyberDeck">
</p>

# Frame CyberDeck

**Steam Frame-focused fork of VR CyberDeck**

Frame CyberDeck is a desktop sideloader and library client built around Valve's Steam Frame. It extends the VR CyberDeck codebase with Steam Frame detection, Quest-to-Frame conversion, SteamOS Devkit deployment, installed-title management, and Frame-aware contribution support.

The project remains usable without a vrSrc API key: local APK sideloading, Quest-to-Frame conversion, Steam Frame deployment, launch, uninstall, and local device management all work independently of catalog access.

> `> FRAME DETECTED. CONVERT. DEPLOY. LAUNCH.`

```
[ TARGET ] STEAM FRAME
[ HOST   ] WINDOWS · LINUX · macOS
[ STACK  ] ELECTRON · REACT · TYPESCRIPT
[ DEPLOY ] STEAMOS DEVKIT · FAUXDROID
```

---

## `// FORK_LINEAGE`

Frame CyberDeck is a fork of **VR CyberDeck** by **DeliciousMeatPop**.

VR CyberDeck itself was built on **ApprenticeVR** by **jimzrt**. Frame CyberDeck keeps that foundation while replacing and extending the device/deployment path for Steam Frame.

This fork is maintained independently for Steam Frame support. Upstream project names are retained here for attribution only.

---

## `// WHAT_THIS_FORK_ADDS`

### Steam Frame detection

- Detects a connected Steam Frame through its native SteamOS ADB interface
- Distinguishes Steam Frame from Android/Quest devices
- Reads Frame storage and network information without treating SteamOS as Android
- Auto-selects the Frame when available

### Quest-to-Frame conversion

Frame CyberDeck can convert a compatible Quest APK before deployment:

1. Inspect the original APK and preserve package/version metadata
2. Patch it with OVR Port
3. Inject the Steam Frame OpenXR bridge/runtime components
4. Align and sign the converted APK
5. Verify the resulting APK
6. Stage it for SteamOS Devkit deployment

The converter uses the prebuilt FrameBridge OpenXR adapter from [FramePort](https://github.com/spoopyghosty0/frameport), pinned to commit `5424e429e46e53fe51497babac124dd91dbb73bf` and verified against its SHA256SUMS manifest.
The normal path keeps the runtime default refresh rate unless explicitly requested.

Frame CyberDeck also applies FramePort's per-game recipes (catalog by spoopyghosty0): for a game the catalog has tested, the conversion merges the recipe's FrameBridge adapter settings (engine-bug fixes such as pose timing, controller vibration, shader fixes) into the APK's settings library, selects the recipe's alternate OVRPort patch set when it asks for one, and injects the matching FramePort shim libraries (the XR shim, the Vulkan shim, and the OpenGL ES shader-fix layer) into the build. The recipe's own suggestions for scale/refresh rate never override your settings.

All credits for the recipes, the FrameBridge adapter, the shim libraries, and the ELF surgery techniques belong to **spoopyghosty0** and the [FramePort](https://github.com/spoopyghosty0/frameport) project (GPLv3, like this project).

The Meta native SSO sign-in and the resumable, size-verified download logic are adapted from **[AXRB](https://github.com/Android-XR-Bridge/AXRB)** (GPLv3), whose native SSO protocol in turn comes from RiftLift's `meta_auth.py` (GPL-3.0-or-later).

The original source APK is never overwritten.

Frame conversion options are available under **Other Settings → Steam Frame Conversion**:

- **Requested display refresh rate:** Runtime default, 72, 80, 90, 96, 108, 120 or 144 Hz.

For development/testing, environment variables can override the saved UI settings:

```fish
set -x FRAME_CYBERDECK_REFRESH_RATE 90
```

### SteamOS Devkit deployment

Frame CyberDeck deploys converted titles through Valve's SteamOS Devkit infrastructure rather than trying to install them directly through SteamOS ADB.

Supported operations include:

- Install/deploy
- Refresh installed titles
- Launch from Frame CyberDeck
- Uninstall
- Preserve package/version/title metadata
- Resolve duplicate managed titles by package

The Devkit connection is paired externally using Valve's normal SteamOS Devkit flow. Frame CyberDeck then reuses the existing Devkit SSH key.

SSH records a host key on the first connection and rejects changed keys on later connections. If a Frame is reset or another Frame uses the same USB forwarding address, verify its identity before removing the old entry from your SSH `known_hosts` file. HTTPS certificate validation is enabled for catalog and mirror transfers.

### Original-source provenance

For managed Steam Frame installs, Frame CyberDeck records which original Quest payload produced the installed Frame build.

That provenance is used for contribution workflows so the application can contribute the **original APK/folder**, including its matching OBB files. Single APK installs made before OBB provenance was recorded must be reinstalled from the original source before contributing. Original local ZIP files are retained after upload.

A local validation tool is included:

```fish
fish scripts/frame-contribution-dry-run.fish com.Icosa.OpenBrush
```

It validates package/version identity, provenance, HWID metadata, and archive creation without performing a network upload.

### Meta owned-title sync

Frame CyberDeck can sign in to your Meta account and contribute owned Quest titles that the server does not have yet.

- **Sign-in:** Meta's native SSO flow opens Meta's own login page (including 2FA) in an embedded window; the password stays with Meta, and the app receives only an encrypted session blob it exchanges for a Quest account token. If sign-in is unavailable, paste a user token instead. The token is validated against Meta and stored encrypted through the OS keychain.
- **Check:** a single entitlement query lists everything the account owns, with each title's latest Android binary (id, version code, package name). These are compared against the server catalog.
- **What becomes a candidate:**
  - Updates — any owned title the server already carries where Meta has a newer version.
  - New titles — only entitlements that were **paid**: purchases, preorders, cross-buy grants, and subscription (Horizon+) claims. Free downloads, promo keys, and trial grants never contribute a title the server does not already have, and neither do demos — even the ones Meta grants through a paid-style offer.
- **Download & contribute:** selected titles download their APK and data files from Meta's entitled CDN into the uploads cache — classic OBB files as well as data shipped under its own name (Unreal `.pak` chunks, named OBBs like `Sounds.obb`) — plus the files of store add-ons the account owns (e.g. music packs), and enter the normal contribution pipeline (HWID metadata, archive creation, `upload.config` upload) unchanged. Downloads are resumable (an interrupted multi-gigabyte OBB continues from where it stopped) and size-verified, disk space is checked before the transfer starts, downloaded versions are cached so re-checks never re-download, and every successfully contributed version is recorded so it is never uploaded twice; a newer version still gets offered.
- Tokens are never written to logs, and download URLs carrying the token are not logged either.

---

## `// LOCAL_SIDELOADING`

Local sideloading does not require a vrSrc API key.

With a Steam Frame connected, you can:

- Install an APK
- Install an extracted game folder
- Install supported ZIP payloads
- Convert compatible Quest APKs automatically
- Deploy converted builds to the Frame
- Launch managed titles
- Uninstall managed titles

The application also retains the original VR CyberDeck Android/Quest paths where they are still applicable.

---

## `// CATALOG_AND_CONTRIBUTIONS`

Catalog access is optional and requires an authorized API key.

When authorized catalog access is configured, Frame CyberDeck can:

- Sync catalog metadata
- Compare installed package/version metadata against the catalog
- Detect packages that are missing or newer
- Prepare eligible contributions using the provided `upload.config`
- Use the recorded original source for Steam Frame contributions
- Contribute owned Meta titles that are new or newer on the account than on the server (see Meta owned-title sync above)

The public/catalog download path is intentionally conservative:

- Maximum 2 concurrent downloads
- Maximum 2 rclone transfers on the protected endpoint
- Request TPS limit of 1 with burst 2

There is no "download all" or bulk-download function.

---

## `// STEAM_FRAME_SETUP`

Conversion requires Python 3, a JDK (Java 21 or newer), and Android SDK build-tools containing `aapt`, `zipalign`, and `apksigner`. Set `ANDROID_SDK_ROOT` and `JAVA_HOME` when those installations are outside their usual locations. Windows SDK tools use their native `.exe`/`.bat` filenames; signing runs through the SDK's Java archive. Devkit deployment also requires `ssh` and `rsync` on the host's PATH.

### 1. Pair SteamOS Devkit access

Pair the Steam Frame once using Valve's SteamOS Devkit client.

Frame CyberDeck expects the normal Devkit SSH key at:

```
~/.config/steamos-devkit/devkit_rsa
```

### 2. Connect the Frame

For development, the Frame host can be supplied explicitly:

```fish
set -x FRAME_CYBERDECK_HOST 192.168.x.x
npm run dev
```

### 3. Install a compatible Quest APK

Use **Manual Install → Install APK File** or drag a supported APK into the sideloader.

Frame CyberDeck will convert the APK, deploy it through SteamOS Devkit, refresh the installed-title list, and make the managed title available for launch/uninstall.

---

## `// BUILD_FROM_SOURCE`

Frame CyberDeck is designed to build without a vrSrc API key. Use Node.js 22.18 or newer and npm; `package-lock.json` is the authoritative dependency lockfile.

```sh
npm ci --legacy-peer-deps
npm run typecheck
npm test
npm run test:node
npm run test:python
npm run build
```

To build an installable package:

| Platform    | Command                     |
| ----------- | --------------------------- |
| Windows x64 | `npm run build:win:x64`     |
| macOS x64   | `npm run build:mac:x64`     |
| Linux x64   | `npm run build:linux:x64`   |
| Linux ARM64 | `npm run build:linux:arm64` |

An authorized catalog key can be supplied at runtime during development:

```sh
FRAME_CYBERDECK_API_KEY=...
```

Never commit an API key.

---

## `// TESTED_FRAME_PATH`

The current Steam Frame path has been exercised end-to-end with Open Brush:

```
original Quest APK
        ↓
metadata inspection
        ↓
OVR Port conversion
        ↓
Frame OpenXR bridge injection
        ↓
zipalign + signing + verification
        ↓
SteamOS Devkit deployment
        ↓
installed-title refresh
        ↓
launch
        ↓
uninstall / reinstall
        ↓
original-source provenance
        ↓
offline contribution packaging validation
```

This validates the complete local path. Live catalog comparison and final `upload.config` transmission require authorized server access.

---

## `// PROJECT_STATUS`

Frame CyberDeck is currently an early Steam Frame-focused fork.

The Frame implementation is functional, but compatibility still depends on the individual Quest application and the capabilities of the conversion/runtime stack. Not every Quest title should be expected to work.

Issues and Frame-specific test reports belong in this repository:

- **[Open an issue](https://github.com/mitch030504/FrameCyberDeck/issues/new)**
- **[FrameCyberDeck repository](https://github.com/mitch030504/FrameCyberDeck)**

---

## `// LICENSE`

GNU GPL v3.

See the repository `LICENSE` file for the full license text.
