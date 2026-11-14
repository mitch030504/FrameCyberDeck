import { app } from 'electron'
import { randomUUID } from 'crypto'
import { promises as fs } from 'fs'
import { join } from 'path'
import { decodeApiKey } from '@shared/keyObfuscation'

/**
 * Short-lived catalog credentials.
 *
 * Official builds carry a public client id and a build secret. At runtime the
 * app exchanges them for a short-lived mirror key (vrsrc /app-key). The key is
 * cached in memory until refreshAfter, and a 403 invalidates it so the next
 * request mints a fresh one. The build secret itself is obfuscated at build
 * time; it is not a long-lived download key.
 */

interface KeyGrant {
  apiKey: string
  baseUri: string
  refreshAt: number
  expiresAt: number
}

interface KeyResponse {
  apiKey?: string
  baseUri?: string
  refreshAfter?: string
  expiresAt?: string
  reason?: string
}

const DEFAULT_ENDPOINT = 'https://vrsrc.fyi/api/mirror/app-key'
const INSTALL_ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/

export class CatalogKeyService {
  private grant: KeyGrant | null = null
  private inFlight: Promise<KeyGrant> | null = null
  private installIdPromise: Promise<string> | null = null

  hasCredentials(): boolean {
    return Boolean(this.clientId() && this.buildSecret())
  }

  getCachedKey(): string {
    if (!this.grant) return ''
    if (Date.now() >= this.grant.expiresAt) {
      this.grant = null
      return ''
    }
    return this.grant.apiKey
  }

  getBaseUri(): string | null {
    return this.grant?.baseUri ?? null
  }

  invalidate(): void {
    this.grant = null
  }

  async ensureKey(force = false): Promise<string> {
    if (!this.hasCredentials()) return ''
    const now = Date.now()
    if (!force && this.grant && now < this.grant.refreshAt) {
      return this.grant.apiKey
    }
    if (this.inFlight) return (await this.inFlight).apiKey

    this.inFlight = this.fetchGrant(force)
    try {
      const grant = await this.inFlight
      this.grant = grant
      return grant.apiKey
    } finally {
      this.inFlight = null
    }
  }

  private clientId(): string {
    return (
      process.env.FRAME_CYBERDECK_CLIENT_ID?.trim() || process.env.VRSRC_CLIENT_ID?.trim() || ''
    )
  }

  private buildSecret(): string {
    const runtime =
      process.env.FRAME_CYBERDECK_BUILD_SECRET?.trim() || process.env.VRSRC_BUILD_SECRET?.trim()
    if (runtime) return runtime
    try {
      return decodeApiKey(process.env.VRSRC_BUILD_SECRET_ENC ?? '')
    } catch (error) {
      console.error('[Catalog Key] Embedded build secret could not be decoded:', error)
      return ''
    }
  }

  private endpoint(): string {
    return process.env.FRAME_CYBERDECK_KEY_URL?.trim() || DEFAULT_ENDPOINT
  }

  private async installId(): Promise<string> {
    if (this.installIdPromise) return this.installIdPromise
    this.installIdPromise = (async () => {
      const path = join(app.getPath('userData'), 'catalog-install-id')
      try {
        const stored = (await fs.readFile(path, 'utf-8')).trim()
        if (INSTALL_ID_PATTERN.test(stored)) return stored
      } catch {
        // generated below
      }
      const generated = randomUUID()
      try {
        await fs.writeFile(path, `${generated}\n`, { mode: 0o600 })
      } catch (error) {
        console.warn('[Catalog Key] Could not persist the install id:', error)
      }
      return generated
    })()
    return this.installIdPromise
  }

  private async fetchGrant(retryPending = true): Promise<KeyGrant> {
    const response = await this.requestKey()
    if (response.status === 503 && response.body.reason === 'key_pending' && retryPending) {
      console.log('[Catalog Key] The server is still preparing the key; retrying in 60 seconds.')
      await new Promise((resolve) => setTimeout(resolve, 60_000))
      return this.fetchGrant(false)
    }
    if (response.status === 403) {
      throw new Error(
        `The catalog key request was rejected (${response.body.reason || 'invalid'}). ` +
          'The build secret may be invalid, retired or the application may be suspended.'
      )
    }
    if (response.status < 200 || response.status >= 300) {
      throw new Error(
        `The catalog key request failed with HTTP ${response.status}` +
          (response.body.reason ? ` (${response.body.reason})` : '')
      )
    }
    const apiKey = response.body.apiKey?.trim()
    const baseUri = response.body.baseUri?.trim()
    if (!apiKey || !baseUri) {
      throw new Error('The catalog key response is missing apiKey or baseUri')
    }
    const now = Date.now()
    const rawRefresh = parseDate(response.body.refreshAfter, now + 60 * 60 * 1000)
    const expiresAt = parseDate(response.body.expiresAt, rawRefresh + 24 * 60 * 60 * 1000)
    const refreshAt = Math.max(now + 1_000, Math.min(rawRefresh, expiresAt - 120_000))
    console.log(
      `[Catalog Key] Received a short-lived catalog key; refresh after ${new Date(refreshAt).toISOString()}.`
    )
    return {
      apiKey,
      baseUri,
      refreshAt,
      expiresAt
    }
  }

  private async requestKey(): Promise<{ status: number; body: KeyResponse }> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 20_000)
    try {
      const response = await fetch(this.endpoint(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: this.clientId(),
          build_secret: this.buildSecret(),
          install_id: await this.installId(),
          app_version: app.getVersion()
        }),
        signal: controller.signal
      })
      let body: KeyResponse = {}
      try {
        body = (await response.json()) as KeyResponse
      } catch {
        // A non-JSON error is still reported by status below.
      }
      return { status: response.status, body }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        throw new Error('The catalog key request timed out')
      }
      throw error
    } finally {
      clearTimeout(timeout)
    }
  }
}

function parseDate(value: string | undefined, fallback: number): number {
  if (!value) return fallback
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export default new CatalogKeyService()
