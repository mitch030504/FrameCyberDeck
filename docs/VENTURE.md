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

## CLI discovery

Frame CyberDeck checks these candidates, in order:

1. `VENTURE_CLI_PATH`
2. `venture-cli` on `PATH`
3. `venture-cli.exe` on `PATH`
4. `/usr/bin/venture-cli`
5. `/usr/local/bin/venture-cli`
6. `/opt/Venture/venture-cli`
7. `%ProgramFiles%\Venture\venture-cli.exe`
8. `%LOCALAPPDATA%\Programs\Venture\venture-cli.exe`

The result is cached briefly, so installing Venture while Frame CyberDeck is
running can be picked up after a refresh or restart. When found, Frame CyberDeck
adds a synthetic **PC · Venture** target to the device list, including the
reported Venture version when available.

## Import, play, and uninstall

1. Select the **PC · Venture** target.
2. Use the normal install/drop flow with a supported release folder or APK
   payload. Frame CyberDeck calls `venture-cli import <folder> --yes`.
3. Wait for the import to complete. A large release can take a long time.
4. Refresh or reopen the target to see the imported titles.
5. Choose **Play** to launch a title through Venture, or **Uninstall** to remove
   it from the Venture library.

Venture IDs are treated as opaque strings. IDs containing `:` are passed as a
single argument and are not split or shell-interpreted.

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

- **Experimental.** Venture paths were not part of the validated 0.2.0 host
  matrix; Linux x64 is the only validated host overall.
- **External dependency.** A missing or incompatible `venture-cli` disables the
  target. Check the path and the CLI's own status output.
- **Launcher must be closed.** Import is blocked while Venture reports its
  launcher open.
- **Separate library.** Imported titles are not Frame installs and do not appear
  in the Frame's installed-title list.
- **Colon IDs.** Current code handles IDs containing `:`, but other tools in the
  Venture ecosystem may not. Report reproducible failures rather than editing
  Venture state by hand.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and
[VERIFICATION.md](VERIFICATION.md).
