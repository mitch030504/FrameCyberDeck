# Catalog credentials and short-lived keys

Status: implemented in 0.2.0 and unchanged in 0.3.0. This document describes the shipped release
behavior and the operator runbook. It does not describe a future proposal.

## What ships in a release build

A release build contains:

- A public client id.
- An obfuscated build credential, encoded at build time.
- The default key endpoint, `https://vrsrc.fyi/api/mirror/app-key`.

The envelope uses AES-256-GCM with a per-build salt and nonce, encoded by
`src/shared/keyObfuscation.ts`. The envelope key material is in the shipped
source. This is **obfuscation**, not confidentiality: a determined user can
recover the build credential from the packaged application, the source, process
memory, or a proxy. The security property is revocability and server-side abuse
control, not secrecy.

Release builds require the dynamic exchange. They fail if a static
`VRSRC_API_KEY` is configured, and a dynamic build does not fall back to a static
key after a failed mint.

Legacy source builds can still use a static `VRSRC_API_KEY` for local
development. That path is not part of 0.3.0 release builds.

## Key exchange

Request:

```http
POST https://vrsrc.fyi/api/mirror/app-key
Content-Type: application/json
```

```json
{
  "client_id": "<public client id>",
  "build_secret": "<build credential>",
  "install_id": "<random UUID>",
  "app_version": "<version>"
}
```

The JSON field name is `build_secret` for protocol compatibility; the value is
the build credential.

Successful response:

```json
{
  "apiKey": "<short-lived key>",
  "baseUri": "https://...",
  "refreshAfter": "<timestamp>",
  "expiresAt": "<timestamp>"
}
```

Client validation:

- The mint request times out after 20 seconds.
- `apiKey` and `baseUri` are required.
- The key must match `[A-Za-z0-9._-]{8,256}`.
- `baseUri` must parse as a URL and use HTTPS.
- If `refreshAfter` or `expiresAt` is missing or invalid, conservative
  fallbacks are used.
- The grant is cached in memory only, never written to disk.
- The minted key is used only with the `baseUri` returned by that grant, not with
  an arbitrary configured base URI.

## Refresh, 403, and 503

| Condition | Behavior |
| --- | --- |
| Cached key before `refreshAfter` | Reused. |
| `refreshAfter` reached | Refreshes before the next protected request. |
| `expiresAt` passed | Key is discarded. |
| HTTP 403 | Invalidates the key; the next request mints once. A repeated failure is reported and catalog features degrade to local-only. |
| HTTP 503 with `reason: "key_pending"` | Waits 60 seconds and retries once. |
| Other non-2xx | Reports HTTP status and reason. |
| Timeout or network error | Reports the failure; local features continue. |

A failed mint does not stop local sideloading, conversion, deployment, launch,
uninstall, or room capture.

## Installation identifier

Path:

```text
<userData>/catalog-install-id
```

- A random UUID generated on first use.
- Written with owner-only permissions where supported (`0600`).
- Sent as `install_id` with key requests.
- Used for per-installation rate limiting and abuse detection.
- Not an account id and not derived from hardware.
- Deleting the file causes a new id to be generated on the next request.

The app version is also sent as `app_version` so the server can distinguish
release channels.

## Operator setup

### Local development

Copy `.env.example` to `.env`:

```sh
VRSRC_CLIENT_ID=<public client id>
VRSRC_BUILD_SECRET=<build credential>
```

Optional development overrides:

```sh
FRAME_CYBERDECK_CLIENT_ID=
FRAME_CYBERDECK_BUILD_SECRET=
FRAME_CYBERDECK_KEY_URL=
```

`FRAME_CYBERDECK_KEY_URL` is honored only in non-packaged development builds and
must be HTTPS. Packaged builds always use the default endpoint.

Verify without printing the credential or key:

```sh
python3 scripts/verify-catalog-key.py
```

### GitHub Actions

Set repository secrets:

- `VRSRC_CLIENT_ID`
- `VRSRC_BUILD_SECRET`

The release workflow requires both credentials during preflight and sets
`FRAME_CYBERDECK_RELEASE_BUILD=1` for the build. The build fails if a static
`VRSRC_API_KEY` is present.

## Rotation and revocation

The release build credential is shared by every installation of a given build.
Treat it as public and revocable.

1. Revoke or suspend the affected credential server-side.
2. Confirm affected clients receive 403 and degrade to local-only.
3. Update the GitHub Actions credential.
4. Rebuild and publish a new version. Do not reuse the old tag.
5. Keep the old release visible long enough for users to see the replacement,
   or mark it clearly.

Per-installation controls require the server to rate-limit and attribute by
`install_id`. The client cannot revoke itself beyond discarding its in-memory
grant.

## Logging and redaction

The app redacts before logs, UI errors, and public support uploads:

- Catalog keys and credential-like key/value fields.
- `-p`/`-p"..."` 7-Zip passwords.
- `X-API-Key` and `Authorization` headers.
- Token and API-key query parameters.
- URL userinfo.
- Credential-like JSON fields such as `build_secret`, `client_secret`,
  `password`, and `apiKey`.

The build credential itself is not covered by a blanket raw-value rule; do not
log it.

**Support logs are uploaded to a public rentry.co paste.** Redaction reduces
exposure but is not a guarantee. Review the log before sharing the short code.
Do not paste a build credential, a Meta token, or an unredacted log into an
issue.

## Threat model and limits

- The build credential is extractable. Obfuscation only raises the cost of
  casual extraction.
- The short-lived key limits the value of a captured key and lets the server
  rate-limit and revoke a build channel.
- The server sees the public client id, the build credential used to
  authenticate the build, the installation id, the app version, and request
  metadata. It does not receive a Meta account token through this exchange.
- TLS protects the request in transit; a local proxy or debugger can still see
  the request and response.
- The app's update check and catalog calls are network activity. Local-only use
  does not require the catalog.

## Code map

| Path | Role |
| --- | --- |
| `src/main/services/catalogKeyService.ts` | Mint, validate, cache, refresh, 403/503 handling, install id. |
| `src/main/services/apiKey.ts` | Key selection; dynamic builds do not fall back to static keys. |
| `src/shared/keyObfuscation.ts` | Build-time credential envelope. |
| `src/main/services/download/downloadProcessor.ts` | Uses the grant baseUri and retries a rejected key. |
| `src/main/services/redaction.ts` | Log/UI redaction. |
| `scripts/verify-catalog-key.py` | Operator mint and mirror probe. |
| `.env.example` | Development credential names and comments. |
| `src/main/services/catalogKeyService.test.ts` | Unit coverage for the key service. |

See [CATALOG.md](../CATALOG.md) for user-facing behavior and
[KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) for release limits.
