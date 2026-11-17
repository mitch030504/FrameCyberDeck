# Releasing

This is the maintainer checklist for a Frame CyberDeck release. It assumes the
repository, CI, and release workflow are under the project's control.

## Version policy

- Frame CyberDeck uses its own `0.x` line. **0.3.0** is the current release;
  **0.2.0** was the first public release.
- 0.1.0 was a development/AUR build and was never published on GitHub.
- Do not reuse upstream VR CyberDeck `1.9.x` or `2.0.0` versions. Those tags
  belong to the upstream project and are present in this repository's history.
- Patch releases use `0.3.x`; additive or compatibility-changing releases use a
  new minor version.
- The release workflow uses the version input as `v$VERSION`, the app version,
  and the release title.

## Required GitHub Actions secrets

| Name | Purpose |
| --- | --- |
| `VRSRC_CLIENT_ID` | Public client id embedded in the release build. |
| `VRSRC_BUILD_SECRET` | Build credential embedded as an obfuscated envelope. Required for the short-lived key exchange. |
| `GITHUB_TOKEN` | Provided by Actions; used for the version bump, draft release, and asset upload. |

Do **not** add `VRSRC_API_KEY` to the release path. Release builds run with
`FRAME_CYBERDECK_RELEASE_BUILD=1`, require the dynamic credentials, and fail if a
static catalog key is present.

The Linux preflight fails if `VRSRC_CLIENT_ID` or `VRSRC_BUILD_SECRET` is empty.

## Pre-release gate

Run from a clean tree:

```sh
npm ci --legacy-peer-deps
npm run typecheck
npm test
npm run test:node
npm run test:python
npm run check:frameport
npx eslint --no-cache .
npm run build
```

Also require:

- CI green on the release commit.
- `package.json` and `package-lock.json` agree at 0.3.0.
- No AUR build debris, `vendor/**/__pycache__`, or untracked build output in the
  release commit.
- `LICENSE` is GPL-3.0-only and matches `package.json`, the README, the AUR
  package, and the release notes.
- `THIRD_PARTY_NOTICES.md` and the vendored FramePort license/notices are
  included in packaged resources.
- The documentation set is current: README, CHANGELOG,
  `docs/INSTALL.md`, `docs/CONVERSION.md`, `docs/ROOM_SCAN.md`,
  `docs/VENTURE.md`, `docs/CATALOG.md`, `docs/KNOWN_LIMITATIONS.md`,
  `docs/VERIFICATION.md`, `docs/security/catalog-credentials.md`, and
  `AUR/README.md`.
- The release claims match `docs/VERIFICATION.md`. Do not label Windows, macOS,
  or Venture as validated.

## Build and draft release

1. Dispatch the **Create Release** workflow with version `0.3.0`.
2. The workflow bumps `package.json`/`package-lock.json` if needed, commits and
   pushes the bump, and builds the matrix.
3. The matrix builds Linux x64/arm64, macOS x64/arm64 DMGs from the macOS
   release job, and Windows x64 installer/portable. Windows `ia32` is not built.
4. The workflow verifies that every platform produced an artifact, creates a
   **draft** release, uploads the assets, and adds `SHA256SUMS`.
5. Matrix failures are blocking. Do not add `continue-on-error` to the release
   build; a missing platform fails the release gate.
6. Inspect the draft before publishing:
   - Every expected asset is present.
   - `SHA256SUMS` matches the uploaded files.
   - The title is `Frame CyberDeck v0.3.0 — TRAILERS_AND_VENTURE`.
   - The release body is the 0.3.0 copy, not an older upstream changelog.
   - Windows/macOS assets are labeled experimental and unverified.
   - The Linux x64 asset is labeled validated.

The workflow refuses to modify a published release or reuse a tag that points
at a different commit. Do not bypass those checks.

## Publish gate

Publish only after:

- The Linux x64 packaged build starts and completes the validated smoke path.
- One Frame engine-path conversion, deploy, launch, and uninstall pass.
- Room capture starts, reports status, and stops.
- The draft asset set and `SHA256SUMS` have been reviewed.
- The release notes and README use the same version, limitations, and license.

Windows and macOS remain downloadable but experimental; they are not a
publication blocker for the Linux-validated release.

## After publishing

- Verify the release page assets and `SHA256SUMS` from a clean browser session.
- Verify the tag points at the built commit.
- Update the AUR package **after** the GitHub release asset exists:
  1. Set `pkgver=0.3.0` and the correct AppImage SHA-256.
  2. Regenerate metadata with `makepkg --printsrcinfo > .SRCINFO`.
  3. Build and install the package locally.
  4. Push the PKGBUILD and regenerated `.SRCINFO` to AUR.
- Confirm `AUR/README.md` still matches the published asset and license.
- Announce the Windows/macOS unverified status explicitly.

## AUR ordering

The AUR package downloads the GitHub release AppImage. It must not be published
before that asset exists. The package is x86_64 only. If the AppImage checksum
or version changes, update both `PKGBUILD` and `.SRCINFO`; a stale `.SRCINFO` is
rejected by AUR tooling.

The source tree currently carries the AUR packaging files but not the release
asset. Verify the `PKGBUILD` checksum against the final 0.3.0 asset and
regenerate `.SRCINFO` before publishing.

## Rollback

- **Draft, not published:** delete the draft release and tag if the build is
  wrong; fix and rebuild.
- **Published:** do not overwrite the release or move the tag. Publish a patch
  release (`0.3.1`) with the fix, mark the broken release clearly, and update
  the AUR package with a higher `pkgrel`/`pkgver`.
- **AUR only:** bump `pkgrel` for a packaging-only fix; bump `pkgver` for a new
  upstream release.
- **Catalog credential incident:** revoke the affected build credential
  server-side, rotate the GitHub secret, and publish a new build. A shipped
  credential cannot be made confidential retroactively.

## Release notes

`.github/release-name.txt` contains one line:

```text
TRAILERS_AND_VENTURE
```

`.github/release-changelog.md` contains the release-specific body. The release
notes generator wraps it with the download table, requirements, upgrade notes,
known limitations, privacy section, and credits. Keep the generated page
consistent with README and CHANGELOG; do not reintroduce the old VR CyberDeck
copy, AGPL text, or `vr-cyberdeck` asset names.
