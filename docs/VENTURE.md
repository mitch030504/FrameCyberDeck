# Venture PC target

Frame CyberDeck can manage a separate PC library through **Venture**, using an
external `venture-cli` installation. Venture support is **experimental** and is
not required for Steam Frame use.

Frame CyberDeck is not affiliated with Venture. Venture is a separate project
under GPL-3.0-or-later (https://github.com/Villagers654/Venture); see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Requirements

- A working Venture installation that provides `venture-cli`.
- The CLI must be discoverable, or its path must be set explicitly.
- Enough disk space for imported releases in Venture's library.
- The Venture launcher must be closed while importing.

Frame CyberDeck does not install Venture, update it, or manage its account.

Some Linux Venture packages — including the 0.2.5 AppImage — ship the launcher
but not `venture-cli`. If your install has no CLI, build it from the matching
Venture source and select the result in Settings (or put it on `PATH`):

```sh
cmake --preset linux-release
cmake --build --preset linux-release --target venture-cli
# build/linux-release/bin/venture-cli
```

## GUI configuration

Open **Settings → Venture PC Target**. The panel reports whether the CLI is
available, its path and version, whether the Venture launcher is open, and the
library folder Venture reports.

- **Browse for venture-cli…** saves an explicit CLI path and re-checks it.
- **Clear Path** removes the saved path and returns to automatic discovery.
- **Re-check** forces a fresh status query.
- **Open Library Folder** opens the folder Venture reports, when available.

The saved setting survives restarts. If no CLI is found, the Target Acquisition
screen keeps a **PC · Venture — CLI NOT FOUND** card visible; its **SET UP**
button opens the Venture settings section directly.

## CLI discovery

Frame CyberDeck checks these candidates, in order:

1. The path saved in Settings
2. `VENTURE_CLI_PATH`
3. `venture-cli` on `PATH`
4. `venture-cli.exe` on `PATH`
5. `/usr/bin/venture-cli`
6. `/usr/local/bin/venture-cli`
7. `/opt/Venture/venture-cli`
8. `%ProgramFiles%\Venture\venture-cli.exe`
9. `%LOCALAPPDATA%\Programs\Venture\venture-cli.exe`

When an explicit path is saved, Frame CyberDeck uses it exclusively; if it is
wrong, clear it to resume auto-discovery. The discovery result is cached
briefly, so installing Venture while Frame CyberDeck is running can be picked
up after a refresh or restart. When found, Frame CyberDeck adds a synthetic
**PC · Venture** target to the device list, including the reported Venture
version and launcher state.

## Import, play, and uninstall

1. Select the **PC · Venture** target.
2. Use **Import into Venture…** (or drag an APK, ZIP, or release folder onto the
   deck) with a supported release. Frame CyberDeck calls
   `venture-cli import <path> --yes`.
3. If the Venture launcher is open, the import is refused before it starts.
   Close the launcher and use **Re-check**.
4. Wait for the import to complete. A large release can take a long time; the
   GUI shows an indeterminate import state rather than a fake percentage.
5. Refresh the PC library to see the imported titles.
6. Choose **Play on PC** in the details dialog or context menu to launch a
   title, or **Remove from PC** to remove it from the Venture library.

Venture IDs are treated as opaque strings. IDs containing `:` are passed as a
single argument and are not split or shell-interpreted. Launch and removal use
the library ID rather than the Android package name, because the two can
differ.

If the launcher is open, import is refused rather than risking a partial
library update. Close the Venture launcher and retry.

Manual imports use the same `venture-cli import` path. Folder paths and IDs are
passed as separate arguments, not through a shell.

## What Frame CyberDeck does not do

- It does not sign in to Meta for Venture.
- It does not download Meta content for Venture.
- It does not convert Quest APKs for the Venture target.
- It does not install or configure Venture itself.
- It does not migrate a Venture library to or from a Steam Frame.

## Account and terms risk

Using Venture may affect your Meta account and is subject to Meta's terms and
Venture's own requirements. You are responsible for how you use the external
tool and for the content you import. Frame CyberDeck only invokes the CLI you
provide.

## Limitations

- **Experimental.** Venture paths were not part of the validated host matrix;
  Linux x64 is the only validated host overall.
- **External dependency.** A missing or incompatible `venture-cli` disables the
  target. The target list keeps a setup card visible so the CLI can be
  configured in Settings; check the path and the CLI's own status output.
- **Launcher must be closed.** Import is blocked while Venture reports its
  launcher open; the GUI shows that state in Settings and on the target card.
- **Separate library.** Imported titles are not Frame installs and do not appear
  in the Frame's installed-title list.
- **Colon IDs.** Current code handles IDs containing `:`, but other tools in the
  Venture ecosystem may not. Report reproducible failures rather than editing
  Venture state by hand.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and
[VERIFICATION.md](VERIFICATION.md).
