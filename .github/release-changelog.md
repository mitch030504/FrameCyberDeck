**Highlights**

- The first public Frame CyberDeck release: Quest-to-Frame conversion, SteamOS Devkit deployment, launch, uninstall, provenance, and a mixed-reality room scan path.
- A vendored FramePort 1.4.1 engine with 194 actionable recipes from 291 catalog entries, plus a built-in fallback converter.
- FramePort's on-Frame agent, deployed over the existing Devkit SSH connection, with an explicit command allowlist.
- An experimental Venture PC target for an external `venture-cli`.
- Short-lived catalog keys for release builds, with no static catalog key in the release path.

**Added**

- FramePort 1.4.1 headless engine at pin `72c71fbbd303886992ab62cbc1aea6287100f935`.
- Conversion engine setting: Automatic (`auto`, default), FramePort engine, and built-in converter.
- FramePort preparation and status UI with a managed Python 3.11+ environment and its own Java, OVRPort, and apksigner tools.
- Mixed Reality Room panel: scan start, status, and stop.
- Venture target: import, list, play, and uninstall through an external CLI.
- Short-lived catalog key exchange using a public client id and an obfuscated build credential.
- `SHA256SUMS` for release assets and the documentation set in this repository.

**Changed**

- `auto` uses FramePort only when the engine is prepared; the built-in converter is the fallback.
- The built-in converter uses OVRPort CLI 1.2.5 and the 194-recipe catalog.
- FramePort recipes merge adapter settings, select alternate OVRPort patch sets, and add matching shims; user scale and refresh choices win.
- New installs use a per-game `launch.sh` with Lepton environment handling, permission repair, single-instance locking, and shortcut appid patching.
- USB Devkit traffic uses ADB port forwards; the network path remains available.
- Data staging supports classic OBB files and raw bundle folders, with original-source provenance.

**Fixed**

- FramePort agent bridge invocation and stripped-ELF section parsing.
- Catalog key retry when a mirror rejects a freshly minted key.
- Runtime default refresh rate now clears a previous recipe override.
- Venture IDs containing `:` and manual import handling.
- 7-Zip passwords and catalog keys in logs, UI errors, and process output.

**Security**

- Release builds require the short-lived exchange and fail if a static `VRSRC_API_KEY` is configured.
- Dynamic builds do not fall back to a static catalog key after a failed mint.
- A minted key is used only with the HTTPS `baseUri` returned by the grant.
- The build credential is obfuscated, extractable, and revocable server-side; obfuscation does not make it confidential.
- Support logs are public rentry.co pastes. Catalog keys, 7-Zip passwords, and
  authorization headers are redacted, but logs may still contain command output.
  Review logs before sharing.

**Verified in 0.2.0**

- Linux x64 packaged build.
- One Frame engine-path conversion with packaged engine resources, Devkit bridge deploy/launch, and uninstall. The app's single-click install button was not exercised in this cycle.
- FramePort preparation from scratch.
- Blade & Sorcery engine build: 13/13 checks.
- Singularity engine build after the stripped-ELF fix: installed and launched.
- Room capture, status, and stop.
- Short-lived key mint HTTP 200 and mirror probe HTTP 206.
- 142 Vitest tests, 71 Node tests (1 skipped), 46 Python tests; typecheck and lint green.

Windows x64 and macOS x64/arm64 are built but experimental and unverified.
Linux x64 is the validated host. FramePort's install-stage hardware decode,
device-side settings, and per-game room provisioning are not applied by Frame
CyberDeck. Captured rooms are not copied into MR titles. Venture is
experimental. See the generated sections below and
`docs/KNOWN_LIMITATIONS.md` for the full limits.
