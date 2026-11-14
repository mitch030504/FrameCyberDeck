import { decodeApiKey } from '@shared/keyObfuscation'
import catalogKeyService from './catalogKeyService'

let legacyKey: string | null = null

/**
 * The catalog key currently in use. Official builds mint a short-lived key at
 * runtime; older builds can still carry a static key in VRSRC_API_KEY_ENC.
 * Dynamic builds never fall back to that static key.
 */
export function getApiKey(): string {
  if (catalogKeyService.hasCredentials()) {
    // Dynamic builds never fall back to a long-lived static key: a failed mint
    // must degrade to local-only so revocation actually works.
    return catalogKeyService.getCachedKey()
  }

  const runtime = process.env.FRAME_CYBERDECK_API_KEY?.trim() || process.env.VRSRC_API_KEY?.trim()
  if (runtime) return runtime

  if (legacyKey === null) {
    try {
      legacyKey = decodeApiKey(process.env.VRSRC_API_KEY_ENC ?? '')
    } catch (error) {
      console.error('[Catalog Key] Embedded catalog key could not be decoded:', error)
      legacyKey = ''
    }
  }
  return legacyKey
}

/**
 * Mint or refresh the short-lived catalog key when this build has dynamic
 * credentials. Returns the static key unchanged for older builds.
 */
export async function ensureApiKey(force = false): Promise<string> {
  if (catalogKeyService.hasCredentials()) {
    try {
      await catalogKeyService.ensureKey(force)
    } catch (error) {
      console.error('[Catalog Key] Could not obtain a catalog key:', error)
    }
  }
  return getApiKey()
}

/** Drop a rejected key so the next request mints a fresh one. */
export function invalidateApiKey(): void {
  catalogKeyService.invalidate()
}

/** Strips the API key out of arbitrary text before it's logged, uploaded, or displayed. */
export function redactApiKey(s: string): string {
  const k = getApiKey()
  return k ? s.split(k).join('[REDACTED]') : s
}
