# Frame CyberDeck implementation notes

Frame CyberDeck is the Steam Frame-focused fork of VR CyberDeck.

## Current Frame path

- Detects native SteamOS ADB separately from Quest/Android devices.
- Converts compatible Quest APKs before deployment.
- Uses OVR Port `3.4.3-23204ea`.
- Pins Quest2Frame source at `c9f1e4d9a705575e103c136d865b869e3d87b256`.
- Builds the current Quest2Frame `frame_bridge.c` locally with verified OpenXR headers.
- Invalidates older cached Frame adapters that do not contain the `Q2F_REFRESH_RATE_V1` capability marker.
- Supports optional per-game refresh-rate requests: runtime default, 72, 80, 90, 120 or 144 Hz.
- Offers an opt-in extended compatibility wrapper based on Quest2Frame's current passthrough adapter. It translates supported full-background passthrough underlays to native alpha blending and corrects validated 1-2 px projection/depth swapchain rectangle overflows.
- Deploys converted titles through SteamOS Devkit and the `fauxdroid` runtime.
- Tracks managed package/version/title metadata.
- Preserves original-source provenance for contribution workflows.

## Development overrides

```fish
set -x FRAME_CYBERDECK_HOST 192.168.x.x
set -x FRAME_CYBERDECK_REFRESH_RATE 90
set -x FRAME_CYBERDECK_EXTENDED_COMPAT 1
npm run dev
```

Both compatibility settings are optional. Without them, FrameCyberDeck keeps the runtime-selected refresh rate and does not install the experimental outer adapter.
