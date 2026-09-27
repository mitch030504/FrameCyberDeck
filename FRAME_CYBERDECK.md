# Frame CyberDeck POC

Steam Frame-specific fork of `DeliciousMeatPop/VRCD`.

## Current POC

- Detects Steam Frame native SteamOS ADB separately from Quest/Android.
- Avoids Android-only package/user queries on the native Frame endpoint.
- Stages a title as `game.apk` plus top-level `obb/*.obb`.
- Uses the installed SteamOS Devkit Client and its Android/`fauxdroid` runtime.
- Routes CyberDeck's normal library, completed-download, ZIP/folder and direct APK installs through the Frame backend.
- Keeps the original Quest installer path as the non-Frame fallback while this fork is being brought up.

## First hardware test

Use a Frame-compatible APK or a `game.apk` already produced by Quest2Frame. The OVR Port/OpenXR conversion pipeline is not integrated yet.

The host defaults to `frame`. Override with `FRAME_CYBERDECK_HOST`.

If SteamOS Devkit Client is installed outside the common Steam library locations, set `STEAMOS_DEVKIT_PYZ` to its `devkit-gui-*.pyz` file.
