# Known limitations

These are the limits of the 0.2.0 release. They are deliberate scope boundaries,
not a promise that a later release will remove them.

## Platforms

- **Linux x64 is the validated host.** The AppImage and `.deb` were exercised
  with the packaged app and one Frame path.
- **Windows x64 is experimental and unverified.** The installer and portable
  build are produced by CI but were not validated with conversion, Devkit
  deployment, or Venture in this release. Builds are unsigned.
- **macOS x64/arm64 are experimental and unverified.** DMGs are ad-hoc signed
  and not notarized. Gatekeeper blocks a normal first launch; macOS 12 or newer
  is required.
- **Linux arm64 is built but unverified.**
- **Windows `ia32` is not supported by the FramePort engine** and is not
  shipped.
- **AUR is x86_64 only.** The AUR package is published after the matching GitHub
  release asset exists.

## Conversion

- **Build-stage only.** The FramePort engine applies build-stage recipes,
  shims, and patches. Frame CyberDeck does not apply FramePort's install-stage
  hardware video decode, device-side settings, or per-game room provisioning.
- **Compatibility is per title and per build.** A recipe is a tested
  configuration, not a guarantee. Some titles will not work.
- **No universal compatibility claim.** A conversion can complete and still
  fail at install or launch.
- **The built-in fallback is less capable.** It does not include FramePort's
  IL2CPP patch stage. Like the FramePort path, it does not add install-stage
  hardware decode or device-side settings.
- **FramePort preparation needs network and disk.** The managed environment and
  tools are downloaded on first prepare; offline preparation is not supported.
- **Refresh rate depends on the runtime.** A requested mode is only used when
  the Frame reports it as supported.
- **Recipe override behavior is title-specific.** Runtime default clears a
  previous recipe refresh override, but other recipe settings still follow the
  recipe's rules.

## Frame and Devkit

- **One Frame path was validated.** The release evidence covers one Frame and
  one engine-path title. The run used the Devkit bridge for deploy, launch, and
  uninstall; the app's single-click install button was not exercised because the
  Frame was not exposed as the app's Steam Frame ADB target in that cycle.
  Other Frame builds, Lepton versions, and room configurations may differ.
- **Lepton is required to launch managed titles.** Frame CyberDeck can start
  the Lepton Development session when an install is refused, but it does not
  install Lepton.
- **Devkit pairing is external.** Pair with Valve's SteamOS Devkit Client first;
  the app reuses the Devkit SSH key.
- **SSH host keys are pinned.** A reset Frame or a reused USB forwarding address
  requires verifying the new key before removing the old `known_hosts` entry.
- **Steam binds a shortcut's runtime at first registration.** A title already
  registered as a fauxdroid/Lepton APK shortcut keeps its APK start command;
  new installs use the per-game launcher.
- **USB tunneling depends on ADB port forwards.** If the cable path is
  unavailable, use the Devkit network path.

## Room scan

- **Capture only.** The agent can capture, report status, stop, and manage room
  data on the Frame. Frame CyberDeck does not copy the captured room into MR
  titles.
- **No per-game provisioning.** Titles that need a room installed in their own
  data do not get one from this release.
- **On-device and environmental.** The app does not preview or edit the mesh.
  Lighting, reflective surfaces, and movement can cause an incomplete scan.
- **One path validated.** Other Frame builds and rooms were not part of the
  0.2.0 evidence.

## Venture

- **Experimental.** Venture support is outside the validated host matrix.
- **External dependency.** A missing or incompatible `venture-cli` disables the
  target. The target list shows a setup card that opens the Venture section in
  Settings; a saved CLI path can be cleared to resume auto-discovery.
- **Launcher must be closed.** Import is refused while Venture reports its
  launcher open; the target card and Settings surface that state.
- **No Meta integration.** Frame CyberDeck does not sign in to Meta, download
  content, or convert APKs for Venture.
- **Account and terms risk.** Using Venture may affect your Meta account and is
  subject to Meta's terms and Venture's requirements.
- **Separate library.** Venture titles are not Frame installs.

## Catalog and privacy

- **Catalog access is optional.** Local sideloading and Frame management do not
  need credentials or a server.
- **The build credential is extractable.** Obfuscation raises the cost of
  casual extraction but does not make the credential confidential. Revocation
  is server-side.
- **No static key in release builds.** A failed mint degrades to local-only for
  catalog features; release builds do not fall back to `VRSRC_API_KEY`.
- **No bulk download.** Downloads are capped at two concurrent titles.
- **Support logs are public.** Uploaded logs go to rentry.co. Redaction covers
  catalog keys, headers, 7-Zip passwords, and token-bearing URLs, but logs may
  still contain command output; review the log before sharing.
- **Network calls exist.** The app checks GitHub for updates, and release builds
  contact the catalog endpoint when catalog features are used.

## Packaging and distribution

- **Unsigned Windows builds.** SmartScreen may warn.
- **Not notarized macOS builds.** Gatekeeper requires an explicit Open Anyway.
- **AUR is maintained separately.** The package points at the GitHub release
  asset; it is not updated before that asset exists.
- **No auto-update guarantee for every package format.** The in-app update check
  is not a substitute for the package manager's upgrade path.
- **Third-party assets carry their own licenses.** See
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and the vendored FramePort
  notices.

See [VERIFICATION.md](VERIFICATION.md) for what was actually exercised and
[RELEASING.md](RELEASING.md) for the release gates.
