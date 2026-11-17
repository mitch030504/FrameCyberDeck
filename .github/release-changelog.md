**Highlights**

- Venture is now a first-class PC target: setup card, saved `venture-cli` path, real `ventureId` handling, and Play/uninstall from details and the context menu.
- Synced to upstream VR CyberDeck 2.0.0 (`8bf949c`), including the restored trailer service, override data, scripts, and diagnostics.
- Trailers are back: lookup and playback in game details, a whole-library **Find Missing Trailers** scan, maintainer overrides, and a report link that pre-fills the issue form.
- The fork's hardened Electron settings are unchanged. Trailer playback runs in a sandboxed, allowlisted YouTube webview; upstream's `sandbox: false` and `webSecurity: false` were not adopted.

**Added**

- Synthetic `PC · Venture` target with a setup card, persisted CLI path, import wording, and launcher warnings.
- Venture 0.2.5 CLI support, including numeric-string `versionCode` values.
- Collapsible trailer drawer with autoplay, a no-trailer fallback, and a report/suggest link.
- **Other Settings → Find Missing Trailers**: live progress, cancel, a written report, and a maintainer review script.
- Trailer override registry with bundled entries, background refreshes, and metadata candidate matching.
- Trailer report workflow for `[Trailer]` issues with validated package names and YouTube IDs.
- ADB push-error diagnostics included in backup restore logs.

**Changed**

- Upstream 2.0.0 merge: trailer services, shared types, IPC, preload API, and Settings integration.
- Backups reject all-empty captures and force-stop the game before a restore.
- Venture UI no longer presents headset-only ADB/OBB controls.
- Package identity stays `frame-cyberdeck` on the fork's `0.x` version line.

**Fixed**

- Venture IDs containing `:`, and Venture CLI 0.2.5 string version codes.
- Trailer scan failures are reported in Settings instead of disappearing.
- Trailer playback no longer raises unhandled rejections when the guest page is not ready.
- `.gitignore` handles `*.tsbuildinfo` and the trailer review ignore file as separate entries.

**Security**

- Trailer guests run sandboxed with no Node integration, no preload, context isolation, and web security on.
- Guest attach and navigation are limited to YouTube-owned HTTPS hosts; popups are denied.
- The parent window keeps `sandbox: true`, context isolation, and no Node integration.
- Renderer `frame-src` is limited to the YouTube hosts required for trailers.
- Trailer reports are length-limited and validated before being stored.

**Verified in 0.3.0**

- 183 Vitest tests, 71 Node tests (1 skipped), and 46 Python tests; typecheck, lint, `check:frameport`, and the production build pass.
- Venture GUI against a real 0.2.5 CLI and library: status, library listing, import wording, and Play wiring. A real launch or uninstall was not performed.
- Trailer lookup, drawer page load, scan/progress/cancel/report, and webview host enforcement under Xvfb. Actual video decode was not confirmed in that environment.
- Linux x64 remains the validated host from 0.2.0. Windows and macOS packages are built but experimental and unverified.

**Upgrade notes**

- From 0.2.0: settings, downloads, backups, and converted titles carry over; no migration is required.
- Set the Venture CLI path in **Other Settings → Venture PC Target** (or `VENTURE_CLI_PATH`) to enable the PC target.
- A fresh YouTube session may show Google's consent screen once; the choice is stored in the dedicated trailer session.

See `docs/KNOWN_LIMITATIONS.md` and `docs/VERIFICATION.md` for the full limits and evidence.
