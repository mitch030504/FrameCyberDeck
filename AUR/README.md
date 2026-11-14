# frame-cyberdeck-bin (AUR)

This directory contains the Arch User Repository package for Frame CyberDeck.
It packages the GitHub release AppImage; it does not build the application from
source.

The AUR package is maintained separately from the GitHub release and is not
affiliated with Valve, Meta, FramePort, Venture, or upstream VR CyberDeck.

## Package

| Item | Value |
| --- | --- |
| Package | `frame-cyberdeck-bin` |
| Version | `0.2.0` |
| Architecture | `x86_64` only |
| Source | `frame-cyberdeck-<version>-x86_64.AppImage` from the GitHub release |
| License | GPL-3.0-only |
| Provides / conflicts | `frame-cyberdeck` |

The package installs under `/opt/frame-cyberdeck`, links
`/usr/bin/frame-cyberdeck`, and installs the desktop entry. It also installs the
project `LICENSE`, `THIRD_PARTY_NOTICES.md`, and the vendored FramePort license
and notices.

The package depends on the Electron runtime libraries plus `python`, `openssh`,
and `rsync`. Python 3.11+ is needed only if you prepare the FramePort engine;
the AUR package's `python` dependency is the rolling Arch package.

Meta sign-in additionally needs working OS key storage (for example
`gnome-keyring` or `kwallet`). It is not a hard package dependency because the
rest of the application works without Meta sign-in.

## Install

With an AUR helper:

```sh
yay -S frame-cyberdeck-bin
```

or:

```sh
paru -S frame-cyberdeck-bin
```

Manually:

```sh
git clone https://aur.archlinux.org/frame-cyberdeck-bin.git
cd frame-cyberdeck-bin
makepkg -si
```

The package downloads the AppImage from the GitHub release during the build. It
requires network access and the release asset must already exist.

## Upgrade

Use the normal AUR upgrade path:

```sh
yay -Syu
```

Settings, downloads, save backups, and logs stay in the Electron `userData`
folder. Existing converted titles are not rebuilt; redeploy from the original
source to pick up the new launcher.

From the 0.1.0 development package: install the 0.2.0 package, then run
**Prepare FramePort engine** before using the FramePort engine. See
[docs/INSTALL.md](../docs/INSTALL.md) for the full upgrade notes.

## Uninstall

```sh
sudo pacman -Rns frame-cyberdeck-bin
```

This removes the application files. It does not remove `userData`; delete
`~/.config/frame-cyberdeck` separately if you want a full reset.

## Build and publish

The release asset must exist before the AUR package is updated. The ordering is
deliberate: the package downloads the AppImage by URL and checksum.

1. Publish the GitHub release and confirm the v0.2.0 AppImage URL and SHA-256.
2. Set `pkgver` in `PKGBUILD` to the release version.
3. Refresh the AppImage checksum, for example with `updpkgsums`.
4. Regenerate metadata:

   ```sh
   makepkg --printsrcinfo > .SRCINFO
   ```

5. Build and install locally:

   ```sh
   makepkg -si
   ```

6. Test launch, Frame detection, and a local sideload if possible.
7. Commit `PKGBUILD` and the regenerated `.SRCINFO`, then push to AUR.

The `.SRCINFO` in the repository may still describe an earlier package until
it is regenerated. Verify the `PKGBUILD` checksum against the v0.2.0 asset and
regenerate `.SRCINFO` before publishing. Do not publish the package while those
values point at a different asset. A stale `.SRCINFO` or a checksum for a
nonexistent `v0.1.0` asset will fail for users.

Do not commit the AppImage, `src/`, `pkg/`, or built `.pkg.tar.zst` files to the
source repository or AUR. They are build artifacts.

## Maintenance notes

- The AUR package is x86_64 only. Linux arm64 users should use the release
  AppImage.
- The package name is `-bin` because it packages the AppImage rather than
  compiling the Electron application.
- If the application version changes without a new AppImage, use a `pkgrel`
  bump; if the AppImage changes, update `pkgver` and the checksum.
- If the license or notices change, update the installed license files in
  `package()`.
- If the release asset is removed, the package cannot be built; publish a
  replacement release or remove the AUR package.

See [docs/RELEASING.md](../docs/RELEASING.md) for the release and AUR ordering.
