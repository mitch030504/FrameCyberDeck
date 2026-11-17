# Install, upgrade, and uninstall

Frame CyberDeck 0.3.0 is the current release. 0.2.0 was the first public release.
Linux x64 is the validated host. Windows x64 and macOS x64/arm64 packages are
built, but experimental and unverified in this release.

## Release assets

| Asset | Platform | Status |
| --- | --- | --- |
| `frame-cyberdeck-0.3.0-x86_64.AppImage` | Linux x64 | **Validated** |
| `frame-cyberdeck-0.3.0-amd64.deb` | Debian/Ubuntu x64 | **Validated** |
| `frame-cyberdeck-0.3.0-arm64.AppImage` | Linux arm64 | Built, unverified |
| `frame-cyberdeck-0.3.0-arm64.deb` | Debian/Ubuntu arm64 | Built, unverified |
| `frame-cyberdeck-0.3.0-x64.dmg` | macOS Intel | Experimental, unverified |
| `frame-cyberdeck-0.3.0-arm64.dmg` | macOS Apple silicon | Experimental, unverified |
| `frame-cyberdeck-0.3.0-setup-x64.exe` | Windows x64 installer | Experimental, unverified |
| `frame-cyberdeck-0.3.0-portable-x64.exe` | Windows x64 portable | Experimental, unverified |

`SHA256SUMS` is attached to the release. Verify downloads before running them.
Windows `ia32` is not shipped; the FramePort engine does not support it.

## What you need

### For Frame management and deployment

- A Steam Frame with Lepton installed.
- SteamOS Devkit access paired once with Valve's SteamOS Devkit Client.
- The Devkit SSH key at `~/.config/steamos-devkit/devkit_rsa` (Linux/macOS path;
  the Devkit client owns the key location on Windows).
- `ssh` on the host.
- `rsync` on Linux/macOS. Windows uses a tar stream over SSH instead.
- USB or network access to the Frame. USB Devkit traffic is tunneled through
  ADB port forwards.

### For conversion

| Engine | Requirements |
| --- | --- |
| **FramePort** | 64-bit Python 3.11+ with `venv` and pip. Network access on first prepare. Disk space for the managed environment and tools. |
| **Built-in fallback** | Python 3, JDK 21+, Android SDK build-tools with `aapt`, `zipalign`, and `apksigner`. Set `ANDROID_SDK_ROOT` and `JAVA_HOME` if needed. |

FramePort preparation creates `<userData>/frameport-engine/venv`, installs the
pinned Python requirements, and downloads its own Java, OVRPort, and apksigner.
It does not use the host JDK or Android SDK.

### For Meta sign-in

Meta sign-in stores the token through Electron's `safeStorage`, which needs OS
key storage. On Linux this normally means a working Secret Service/keyring. The
application refuses to save a token when secure storage is unavailable.

## First run

1. Start Frame CyberDeck.
2. Connect the Frame by USB or the Devkit network path and select it in the
   device list. A connected Frame is selected automatically when available.
3. If the Frame is not paired, pair it in **SteamOS Devkit Client** first. The
   app reuses that pairing and key.
4. For the FramePort engine, open **Other Settings → Steam Frame Conversion**,
   choose the engine, and select **Prepare FramePort engine**. Wait for the tool
   status to report Java, OVRPort, and apksigner as installed.
5. Drop a compatible APK, ZIP, or game folder onto the deck, or use **Install
   APK** / **Install Folder**.
6. Wait for conversion and deployment, refresh installed titles if needed, then
   launch from the deck.

The original source APK is never overwritten.

## Upgrade

### From 0.2.0

Settings, downloads, save backups, and converted titles carry over; no migration
is required. Set the Venture CLI path in **Other Settings → Venture PC Target**
or with `VENTURE_CLI_PATH` to enable the PC target. Trailer playback and the
**Find Missing Trailers** scan are available immediately.

### From the 0.1.0 development/AUR build

0.1.0 was never published on GitHub. If you installed it from AUR or built it
locally:

1. Install the 0.3.0 package after the AUR package is updated.
2. Your settings, downloads, save backups, and logs remain in `userData`.
3. Existing converted titles are **not** rebuilt. Redeploy from the original
   source if you want the new per-game launcher and current conversion output.
4. Run **Prepare FramePort engine** before selecting the FramePort engine or
   relying on `auto` to use it.
5. If an old single-APK install has no recorded OBB provenance, reinstall it
   from the original source before contributing.

### From upstream VR CyberDeck 1.9.x

Frame CyberDeck is a separate application and version line. It installs side by
side with upstream VR CyberDeck; settings, downloads, and library state do not
migrate. Keep the upstream installation if you still need it.

### From a pre-release 0.2.0 build

If you built 0.2.0 locally before the public release, reinstall the final
package. Settings are compatible. Re-run **Prepare FramePort engine** only if
the managed environment was removed or is reported incomplete.

## Uninstall

### Linux AppImage

Delete the AppImage. If you created a desktop entry manually, remove it too.

### Debian/Ubuntu package

```sh
sudo apt remove frame-cyberdeck
```

### AUR package

```sh
sudo pacman -Rns frame-cyberdeck-bin
```

The AUR package installs under `/opt/frame-cyberdeck` and `/usr/bin/frame-cyberdeck`.
The AUR package is only published after the matching GitHub release asset
exists; see [AUR/README.md](../AUR/README.md).

### Windows

Use **Settings → Apps → Installed apps → Frame CyberDeck → Uninstall**, or
delete the portable executable. Portable builds keep data in `userData`; delete
that folder separately if you want a full reset.

### macOS

Quit the app and move **Frame CyberDeck** from Applications to the Trash.
Remove `~/Library/Application Support/frame-cyberdeck` separately for a full
reset.

## Data locations

The default Electron `userData` folder is:

| Platform | Path |
| --- | --- |
| Linux | `~/.config/frame-cyberdeck` |
| macOS | `~/Library/Application Support/frame-cyberdeck` |
| Windows | `%APPDATA%\frame-cyberdeck` |

Important contents:

| Path under `userData` | Contents |
| --- | --- |
| `settings.json` | Application settings, including engine and refresh-rate choice |
| `downloads/` | Default download and release staging folder |
| `downloads/uploads/` | Upload staging and contribution archives |
| `save-backups/` | Local save backups and backup profiles cache |
| `frameport-engine/` | Managed venv, FramePort home, and scratch builds |
| `catalog-install-id` | Random installation id used for catalog key minting |
| `logs/main.log` | Application log used by the log uploader |
| `vrp-data/` | Metadata, thumbnails, and other caches |

**Reset App Data** in Settings clears caches, logs, and preferences. It is not a
full uninstall and does not necessarily remove downloads or save backups.
Removing the whole `userData` folder does remove settings, downloads, backups,
provenance, logs, and the managed engine.

## Unsigned builds

### Windows

Windows builds are unsigned. SmartScreen may show a warning. Verify the
downloaded file against `SHA256SUMS` first, then use **More info → Run anyway**
if you accept the risk.

### macOS

macOS builds are ad-hoc signed and not notarized. Gatekeeper blocks a normal
first launch. After verifying `SHA256SUMS`, use **System Settings → Privacy &
Security → Open Anyway**. macOS 12 Monterey or newer is required.

### Linux

AppImage:

```sh
chmod +x frame-cyberdeck-0.3.0-x86_64.AppImage
./frame-cyberdeck-0.3.0-x86_64.AppImage
```

Debian/Ubuntu:

```sh
sudo apt install ./frame-cyberdeck-0.3.0-amd64.deb
```

AppImage sandboxing and missing FUSE are distribution-specific; use the `.deb`
if the AppImage cannot start.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Frame not detected | Devkit pairing, USB data cable, `FRAME_CYBERDECK_HOST`, and the Devkit client's connection. |
| Deploy fails with a pairing message | Pair the Frame in **SteamOS Devkit Client** and confirm the Devkit key exists. |
| Install refused because Lepton is not running | Start Lepton Development (`steam://rungameid/3056000`) or let the launcher start it and retry. |
| FramePort engine is not prepared | Open **Steam Frame Conversion** and choose **Prepare FramePort engine**. Check Python 3.11+, network, and free disk space. |
| Built-in conversion cannot find tools | Install JDK 21+ and Android SDK build-tools; set `ANDROID_SDK_ROOT` and `JAVA_HOME`. |
| Catalog download fails with 403 | The build credential may be retired or suspended; local sideloading still works. See [docs/CATALOG.md](CATALOG.md). |
| Meta token cannot be saved | OS key storage is unavailable; unlock the keyring or use a user-token fallback only if secure storage is available. |
| Venture target is missing | Install `venture-cli` or set `VENTURE_CLI_PATH`. See [docs/VENTURE.md](VENTURE.md). |
| AppImage will not start | Try the `.deb`; check FUSE and sandbox configuration for your distribution. |

Support logs are uploaded to a **public** rentry.co paste. Review the log before
sharing. More limits are in [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).
