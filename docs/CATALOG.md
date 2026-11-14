# Catalog access, credentials, and privacy

Catalog access is optional. Local APK sideloading, conversion, Frame
deployment, launch, uninstall, and room capture work without catalog
credentials.

Frame CyberDeck is not affiliated with vrSrc or the upstream VR CyberDeck
project. The catalog endpoint and credential process are operated separately
from this repository.

## Release builds

Release builds carry:

- A public client id.
- An obfuscated build credential.
- The key-exchange endpoint, by default
  `https://vrsrc.fyi/api/mirror/app-key`.

At runtime the app sends the client id, build credential, a random installation
id, and the app version. The server returns a short-lived `apiKey`, a `baseUri`,
`refreshAfter`, and `expiresAt`. The key is held in memory, refreshed after
`refreshAfter`, and used only with the HTTPS `baseUri` in the grant.

Release builds **require** the short-lived exchange. They fail the build if a
static `VRSRC_API_KEY` is configured, and a dynamic build never falls back to a
static key after a failed mint.

The build credential is obfuscated, not confidential. It can be recovered from
a shipped build. Revocation is server-side. See
[security/catalog-credentials.md](security/catalog-credentials.md).

## Source builds

A source build without catalog credentials stays local-only. For catalog
development, copy `.env.example` to `.env` and set:

```sh
VRSRC_CLIENT_ID=<public client id>
VRSRC_BUILD_SECRET=<build credential>
```

Do not commit the credential. A fork needs its own developer application and
credentials; do not publish a build containing someone else's.

Legacy source builds may still use `VRSRC_API_KEY` for a static key. That path is
not used by 0.2.0 release builds and is not a fallback for dynamic builds.

## Key lifecycle

| Event | Behavior |
| --- | --- |
| First catalog request | Mints a key and caches it in memory. |
| `refreshAfter` reached | Refreshes before the next protected request. |
| `expiresAt` passed | Discards the cached key. |
| HTTP 403 | Invalidates the key; the next request mints once more, then reports the failure. |
| HTTP 503 `key_pending` | Waits 60 seconds and retries once. |
| Other non-2xx | Reports the HTTP status and reason. |
| Timeout | The mint request times out after 20 seconds. |
| Invalid response | Rejects a missing key/baseUri, a malformed key, or a non-HTTPS baseUri. |

A failed mint degrades to local-only behavior for catalog features. It does not
stop local sideloading or Frame management.

## Installation id

The app stores a random UUID in:

```text
<userData>/catalog-install-id
```

It is created on first use with owner-only permissions where the platform
supports them. It is sent with key requests so the server can apply
per-installation rate limits and abuse controls. It is not an account id and is
not derived from hardware. Deleting the file causes a new id to be generated.

## Contributions and Meta sync

Contribution and Meta owned-title sync are optional and require more than a
catalog key:

- A configured server or remote with the expected `upload.config`.
- A writable download/upload location with enough disk space.
- For Meta sync, a Meta account and OS key storage for the token.

Meta sign-in uses Meta's native SSO flow in an embedded window. The password
stays with Meta; the app receives the session result and stores the Quest token
through Electron `safeStorage`. If secure storage is unavailable, token storage
is refused. A user-token paste is the fallback.

Frame CyberDeck only contributes titles the account owns under the rules in the
UI: updates for titles already present, and new titles only for paid
entitlements. Free downloads, promo keys, trials, and demos do not create new
server entries. Contributed versions are recorded so the same release is not
uploaded twice.

Meta sync is subject to Meta's terms. Frame CyberDeck is not affiliated with
Meta.

## Download limits

- Maximum **2** concurrent downloads.
- Public catalog path: 2 rclone transfers, TPS limit 1 with burst 2.
- A user-configured mirror may use up to 4 transfers.
- There is no “download all” or bulk-download function.

## Support logs and privacy

The app checks GitHub for updates. Release builds may contact the catalog
endpoint when catalog features are used. Local-only use does not require the
catalog.

**Support logs are uploaded to a public rentry.co paste.** The uploader redacts
catalog keys, 7-Zip passwords, authorization headers, token-bearing URLs, and
credential-like key/value fields. It does not apply a blanket raw-value rule to
every possible credential, so do not log the build credential. Some
command-line credential patterns are redacted, but logs may still contain
command output. Review the log before sharing the short code. Do not paste the
build credential, a Meta token, or an unredacted log into an issue.

## Troubleshooting

| Symptom | Meaning |
| --- | --- |
| 403 on mint | The build credential is invalid, retired, suspended, or the application is not authorized. Local mode still works. |
| 503 `key_pending` | The server is still preparing the key; the app waits 60 seconds once. |
| Catalog downloads fail but local installs work | The mint or grant failed; check network, endpoint, and build credentials. |
| Meta token cannot be saved | OS key storage is unavailable. Unlock the keyring or use the token fallback only if secure storage is available. |
| No catalog features | The build has no credentials or the server is not configured. This is normal for local-only use. |

See [security/catalog-credentials.md](security/catalog-credentials.md) for the
operator runbook and [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for release
limits.
