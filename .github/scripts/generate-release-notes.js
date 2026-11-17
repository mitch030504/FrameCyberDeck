#!/usr/bin/env node
'use strict'
const fs = require('fs')
const path = require('path')

const version = process.argv[2]
if (!version) {
  process.stderr.write('Usage: generate-release-notes.js <version>\n')
  process.exit(1)
}

const root = path.join(__dirname, '..', '..')
const name = fs.readFileSync(path.join(root, '.github', 'release-name.txt'), 'utf-8').trim()
const changelog = fs
  .readFileSync(path.join(root, '.github', 'release-changelog.md'), 'utf-8')
  .trim()
const v = version

const notes = `# Frame CyberDeck v${v} — \`${name}\`

> \`> FRAME DETECTED. CONVERT. DEPLOY. LAUNCH.\`

Frame CyberDeck is an independent, GPL-3.0-only fork of **VR CyberDeck** by
DeliciousMeatPop, focused on Valve's Steam Frame. It is not an official VR
CyberDeck release and is not affiliated with or endorsed by Valve, Meta,
FramePort, Venture, or the VR CyberDeck maintainer.

0.2.0 was the first public release. 0.1.0 was a development/AUR build and was
never published on GitHub.

---

## \`// CHANGELOG\`

${changelog}

---

## \`// DOWNLOAD\`

| File | Platform | Status |
|------|----------|--------|
| \`frame-cyberdeck-${v}-x86_64.AppImage\` | Linux x64 | **Validated** |
| \`frame-cyberdeck-${v}-amd64.deb\` | Debian/Ubuntu x64 | **Validated** |
| \`frame-cyberdeck-${v}-arm64.AppImage\` | Linux ARM64 | Unverified |
| \`frame-cyberdeck-${v}-arm64.deb\` | Debian/Ubuntu ARM64 | Unverified |
| \`frame-cyberdeck-${v}-x64.dmg\` | macOS x64 | Experimental, unverified |
| \`frame-cyberdeck-${v}-arm64.dmg\` | macOS arm64 | Experimental, unverified |
| \`frame-cyberdeck-${v}-setup-x64.exe\` | Windows x64 installer | Experimental, unverified |
| \`frame-cyberdeck-${v}-portable-x64.exe\` | Windows x64 portable | Experimental, unverified |

Verify downloads against the attached \`SHA256SUMS\`.

**Windows:** builds are unsigned; SmartScreen may warn. Check the SHA-256
before choosing “More info → Run anyway”.

**macOS:** builds are ad-hoc signed and not notarized. Gatekeeper will block a
normal launch. Use System Settings → Privacy & Security → Open Anyway, and
only after verifying the checksum. macOS 12 Monterey or newer is required.

**Linux AppImage:**

\`\`\`
chmod +x frame-cyberdeck-${v}-x86_64.AppImage
./frame-cyberdeck-${v}-x86_64.AppImage
\`\`\`

---

## \`// REQUIREMENTS\`

- **Steam Frame path:** SteamOS Devkit pairing, \`ssh\`, and (Linux/macOS)
  \`rsync\`; Lepton installed on the Frame.
- **FramePort engine:** 64-bit Python 3.11+ with \`venv\` and pip. Prepare the
  engine once in **Other Settings → Steam Frame Conversion**; it downloads its
  own Java, OVRPort and apksigner on first use. Network access is required.
- **Built-in fallback converter:** Python 3, JDK 21+, and Android SDK
  build-tools with \`aapt\`, \`zipalign\` and \`apksigner\`.
- **Venture PC target (experimental):** an existing \`venture-cli\` install.
  Frame CyberDeck does not install Venture or sign in to Meta for it. If the
  CLI is not on \`PATH\`, set its path in **Other Settings → Venture PC Target**.

---

## \`// UPGRADE NOTES\`

- From a 0.2.0 release: settings, downloads, backups, and converted titles carry
  over. No migration is required. Set the Venture CLI path if it is not on
  \`PATH\`.
- From a 0.1.0 development/AUR install: settings and downloads carry over.
  Existing converted titles are not rebuilt; redeploy from the original source
  to pick up the new per-game launcher.
- From upstream VR CyberDeck 1.9.x or 2.0.0: this is a separate application and
  version line. It installs side by side; settings and library state do not
  migrate.
- Run **Prepare FramePort engine** before the first engine conversion. Until
  then \`auto\` uses the built-in converter.

---

## \`// KNOWN LIMITATIONS\`

- Linux x64 is the validated host in this release. Windows and macOS packages
  are produced but were not validated with FramePort, Devkit deployment, or
  Venture in this cycle.
- FramePort compatibility is per title and per build. Recipes are configurations
  from FramePort's catalog, not a guarantee that every recipe works.
- The FramePort engine path applies build-stage patches. FramePort's install-
  stage hardware video decode and device-side settings are not applied by Frame
  CyberDeck's deployment path.
- Room scan captures and stores a room on the Frame. Captured rooms are not yet
  copied into individual MR titles.
- Venture support is experimental and requires an external CLI. Close the
  Venture launcher before importing.
- Trailer playback needs network access. A fresh YouTube session may show
  Google's consent screen once; the choice is stored in the dedicated session.
  Actual video decode was not validated in the release test environment.
- The build credential is obfuscated, not secret: it can be recovered from any
  shipped build. Revocation is server-side. Local sideloading and Frame
  management work without catalog credentials.

---

## \`// PRIVACY AND CREDENTIALS\`

Official builds exchange a public client id and a build credential for a
short-lived mirror key at \`https://vrsrc.fyi/api/mirror/app-key\`, sending a
random installation id and the app version. The minted key is held in memory,
refreshed, and used only for catalog downloads. The app also checks GitHub for
updates. Local sideloading and conversion do not use the catalog.

Support logs uploaded from Settings go to a **public** rentry.co paste.
Credentials and command lines are redacted, but review the log before sharing.

---

## \`// CREDITS AND LICENSE\`

Frame CyberDeck is GPL-3.0-only. Source is available at this tag. It includes
vendored FramePort 1.4.1 (GPL-3.0-only) by spoopyghosty0, and uses OVRPort,
AXRB/RiftLift, and optional Venture. See \`THIRD_PARTY_NOTICES.md\` and
\`vendor/frameport/THIRD_PARTY_NOTICES.md\`.

Built on VR CyberDeck by DeliciousMeatPop, which was built on ApprenticeVR by
jimzrt.

---

## \`// FEEDBACK\`

- [Open an issue](https://github.com/mitch030504/FrameCyberDeck/issues/new) in
  this repository, not the upstream tracker.
- Include the log from **Other Settings → Upload Log** only after reviewing the
  public paste.

\`> v${v} — END_OF_FILE\``

process.stdout.write(notes)
