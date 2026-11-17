# Audit fixes — 2026-10-11

> **Historical document.** This records the state of the 2026-10-11 audit and is
> superseded by [docs/VERIFICATION.md](docs/VERIFICATION.md) for the 0.2.0
> release. Counts, package versions, and limits below are **as of the audit
> date** unless explicitly updated.

The 20 reported code findings were addressed at the time of the audit. One
upstream dependency advisory remained open; details and verification limits are
below.

## Changes

| Finding                           | Fix                                                                                                                                                                                                                              |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Download deletion              | Queue destinations identify individual release folders. Legacy paths are checked, active transfers settle before deletion, and failed deletion keeps the queue entry.                                                            |
| 2. Original ZIP deletion          | The upload transport retains its input. Only callers that created temporary archives remove them.                                                                                                                                |
| 3. Renderer privileges            | Enabled the sandbox and context isolation, restricted navigation and external links, validated IPC senders, restricted local images to the thumbnail directory, removed the generic Electron bridge, and added a production CSP. |
| 4. Install script traversal       | APK, push, and pull paths are constrained to the release directory, including real paths reached through symlinks.                                                                                                               |
| 5. Mirror credential leakage      | Mirror downloads and metadata requests omit the catalog header. Download subprocesses use the explicitly constructed environment.                                                                                                |
| 6. Network trust                  | Restored HTTPS certificate checks. SSH remembers host keys and rejects changed keys. Updated vulnerable dependencies where patched versions are available.                                                                       |
| 7. Frame title collisions         | New Devkit IDs use the verified APK package. Updates preserve a legacy ID only when its metadata identifies the same package. Foreign or unmanaged ID collisions are rejected.                                                   |
| 8. False install success          | All APK installation paths treat `false` as failure and propagate the failure to callers.                                                                                                                                        |
| 9. Offline startup                | Dependency initialization checks local tools without a mandatory GitHub probe. Failures leave the initialization state so retries can run.                                                                                       |
| 10. Windows conversion tools      | Detects SDK `.exe` and `.bat` filenames, discovers common SDK locations, and invokes the signing Java archive on Windows.                                                                                                        |
| 11. Dropped installation requests | Completed downloads can enter the installation queue while download slots are occupied. Requests are marked as installing, and failures reach the renderer.                                                                      |
| 12. Converted APK metadata        | Reads package/version identity with `aapt` on both conversion branches and writes metadata for already converted APKs.                                                                                                           |
| 13. Original OBB provenance       | Records matching original OBB paths and includes them in single APK contributions. Already converted inputs are not recorded as original sources. Older incomplete or mismatched provenance is rejected.                         |
| 14. Backup traversal              | Backup IDs must be safe directory names. Manifest IDs must match their directories. Upload preparation directories also validate package IDs before deletion.                                                                    |
| 15. Release integrity             | Published releases are preserved, existing tags must match the built commit, empty artifacts fail, failed retries return failure, version bumps update the lockfile, and macOS builds check out the release tag.                 |
| 16. Concurrent Frame work         | Serializes conversion and deployment, uses unique tool download files, and serializes source registry updates.                                                                                                                   |
| 17. Upload cancellation/retry     | Cancellation blocks subsequent preparation and transfers. Retry retains source paths and OBB provenance. Re-adding a failed local item replaces its old entry, and an active cancelled operation cannot be replaced.             |
| 18. JSON persistence              | Queue, backup manifests, and Frame provenance use serialized atomic replacements. Queue state is flushed on confirmed close.                                                                                                     |
| 19. Resume concurrency            | Resume requests use the scheduler and its limits. Active pipelines, duplicate requests, statuses, and late progress from cancelled processes are guarded.                                                                        |
| 20. Disabled trailers             | Removed the trailer override/report services, exclusive tests, UI, IPC, webview types, and YouTube session overrides. Shared description metadata remains in use. Trailer metadata lookup is separate and was not fully removed. |

> **Update (2026-11-17):** the fork merged upstream v2.0.0, which restores the trailer scan/override services and the third-party trailer player. The player is confined to a dedicated YouTube session and an allowlisted, sandboxed webview; item 20 above is superseded.

At the audit date, the stale pnpm lockfile had been removed; npm and
`package-lock.json` defined the build, including CI. The README then documented
the conversion prerequisites and SSH behavior that existed at that time. For
current prerequisites, use [docs/INSTALL.md](docs/INSTALL.md) and
[docs/CONVERSION.md](docs/CONVERSION.md).

## Verification (at audit time)

- Clean installation: `npm ci --legacy-peer-deps --ignore-scripts`.
- Main and renderer type checks and the production build: passed.
- Vitest: 106 tests passed.
- Node tests: 70 passed, one existing test skipped.
- Python converter/bridge tests: 21 passed.
- ESLint: zero errors; 20 existing warnings remain.
- Linux x64 unpacked packaging: passed.
- Isolated Electron check: the sandboxed preload exposes the application API, invokes IPC from the expected main frame, keeps Node/Electron globals unavailable to the page, and blocks navigation.
- Release retry script failure/success checks and workflow YAML/shell checks: passed. GitHub workflows were not executed.

## Remaining risk and limits (at audit time)

`npm audit` reports two high severity package entries, down from 35 affected packages. Both remaining entries come from the same unpatched `node-forge` advisory through `@devicefarmer/adbkit`. The [upstream advisory](https://github.com/advisories/GHSA-86w9-cpqp-85rv) lists no patched version. The audit result is not suppressed.

Electron 43.7.9 preserves the project's 32-bit Windows target. [Electron 43 is the final line shipping 32-bit builds](https://releases.electronjs.org/release/v43.0.0); it will need a major upgrade when that support is retired. The patched Electron downloader is overridden to version 5.1.0; its CommonJS API and Linux packaging were checked.

Upload cancellation waits for an already running ADB pull or file copy to finish before stopping subsequent work. SSH accepts and records a key on the first connection; verify a device before replacing a changed known host key, especially when changing headsets on a shared USB forwarding address.

At the audit time, live Frame/Quest conversion, installation, contribution,
backup/restore, Windows/macOS packages, and release publishing were not
exercised, and no headset state was changed. Later 0.2.0 work superseded the
headset part of that statement: one Linux x64 host, one Frame engine-path
conversion/deploy/launch/uninstall, Blade & Sorcery build checks, Singularity
install/launch, and room capture were exercised. Windows/macOS remain
unverified. See [docs/VERIFICATION.md](docs/VERIFICATION.md).
