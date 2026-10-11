import { app, BrowserWindow, safeStorage } from 'electron'
import { promises as fs } from 'fs'
import { join } from 'path'
import { createHash, timingSafeEqual } from 'crypto'
import { EventEmitter } from 'events'

const GRAPH_URI = 'https://graph.oculus.com/graphql'
const USER_AGENT = 'OculusGraphQLApiLib/1.0'

// Native SSO protocol adapted from AXRB (github.com/Android-XR-Bridge/AXRB)
// and RiftLift's meta_auth.py (both GPLv3): Meta hands the app a short-lived
// challenge, the user signs in on auth.meta.com (password and 2FA stay with
// Meta), and the app receives an encrypted session blob it exchanges for a
// user token. The app never sees the password.
const SSO_APP_ID = '512466987071624'
const SSO_CLIENT = `FRL|${SSO_APP_ID}|01d4a1f7fd0682aea7ee8ae987704d63`
const META_GRAPH_URI = 'https://meta.graph.meta.com'
// The profile token must be minted for Meta's own Quest app to be accepted
// by the Quest delivery endpoints.
const SSO_PROFILE_APP_ID = '1582076955407037'

/** User access tokens always start with FRL/OC; OC|... is an app token. */
export function isUserTokenShape(token: string): boolean {
  return (
    (token.startsWith('FRL') || token.startsWith('OC')) &&
    !token.includes('|') &&
    !token.includes('%')
  )
}

/** POST one of graph.oculus.com's persisted queries. The token is never logged. */
export async function graphRequest<T>(
  docId: string,
  variables: Record<string, unknown>,
  token: string
): Promise<T> {
  const body = new URLSearchParams({
    access_token: token,
    doc_id: docId,
    variables: JSON.stringify(variables)
  })
  // graph.oculus.com intermittently answers 500 on first request; retry once.
  let response: Response | undefined
  for (let attempt = 0; attempt < 2 && !response?.ok; attempt++) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 1000))
    response = await fetch(GRAPH_URI, {
      method: 'POST',
      headers: { 'User-Agent': USER_AGENT },
      body
    })
    if (!response.ok && response.status < 500) break
  }
  if (!response?.ok) {
    throw new Error(`Meta request failed (${response?.status})`)
  }
  const data = (await response.json()) as { errors?: { message: string }[] }
  if (data.errors?.length) {
    throw new Error(data.errors.map((error) => error.message).join('; '))
  }
  return data as T
}

interface ViewerResponse {
  data: {
    viewer: {
      user?: { alias?: string; display_name?: string }
    }
  }
}

/** Display name from a viewer response, tolerating both spellings Meta uses. */
function viewerName(viewer: ViewerResponse['data']['viewer']): string | undefined {
  return viewer.user?.alias || viewer.user?.display_name
}

class MetaAccountService extends EventEmitter {
  private get tokenFilePath(): string {
    return join(app.getPath('userData'), 'meta-account.json')
  }

  /** Decrypt the stored token; empty string when absent or unreadable. */
  async getToken(): Promise<string> {
    try {
      const stored = JSON.parse(await fs.readFile(this.tokenFilePath, 'utf-8')) as {
        token?: string
      }
      const encrypted = Buffer.from(stored.token ?? '', 'base64')
      return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(encrypted) : ''
    } catch {
      return ''
    }
  }

  /** Validate a token against Meta, then store it encrypted (OS keychain-backed). */
  async setToken(token: string): Promise<{ ok: boolean; name?: string; error?: string }> {
    const trimmed = token.trim()
    if (!isUserTokenShape(trimmed)) {
      return {
        ok: false,
        error:
          "Token doesn't look like a Meta user token. It must start with FRL and contain no | or %."
      }
    }
    try {
      const viewer = await graphRequest<ViewerResponse>('4149322231793299', {}, trimmed)
      const name = viewerName(viewer.data.viewer)
      if (!safeStorage.isEncryptionAvailable()) {
        return { ok: false, error: 'OS key storage is unavailable; cannot store the token safely.' }
      }
      await fs.writeFile(
        this.tokenFilePath,
        JSON.stringify({
          token: safeStorage.encryptString(trimmed).toString('base64')
        }),
        'utf-8'
      )
      this.emit('account-changed', { authenticated: true, name })
      return { ok: true, name }
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : 'Token validation failed'
      }
    }
  }

  async logout(): Promise<void> {
    await fs.rm(this.tokenFilePath, { force: true }).catch(() => {})
    this.emit('account-changed', { authenticated: false })
  }

  async getStatus(): Promise<{ authenticated: boolean; name?: string }> {
    const token = await this.getToken()
    if (!token) return { authenticated: false }
    try {
      const viewer = await graphRequest<ViewerResponse>('4149322231793299', {}, token)
      return {
        authenticated: true,
        name: viewerName(viewer.data.viewer)
      }
    } catch {
      // A stored token that no longer validates is as good as logged out.
      return { authenticated: false }
    }
  }

  /**
   * Meta native SSO login: the user signs in (2FA included) on Meta's own
   * auth page in an embedded window; the app receives only the callback's
   * encrypted session blob and exchanges it for a Quest account token.
   */
  async loginWithSso(): Promise<{ ok: boolean; name?: string; error?: string }> {
    let challenge: { native_sso_token?: string; native_sso_etoken?: string }
    try {
      const response = await fetch(`${META_GRAPH_URI}/webview_tokens_query`, {
        method: 'POST',
        headers: { 'User-Agent': USER_AGENT },
        body: new URLSearchParams({ access_token: SSO_CLIENT })
      })
      if (!response.ok) throw new Error(`Meta sign-in request failed (${response.status})`)
      challenge = (await response.json()) as {
        native_sso_token?: string
        native_sso_etoken?: string
      }
      if (!challenge.native_sso_token || !challenge.native_sso_etoken) {
        throw new Error('Meta did not return a sign-in challenge.')
      }
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : 'Sign-in failed' }
    }
    const confirmUrl = `https://auth.meta.com/native_sso/confirm?${new URLSearchParams({
      native_app_id: SSO_APP_ID,
      source_app_id: SSO_APP_ID,
      native_sso_etoken: challenge.native_sso_etoken,
      utm_source: 'frame-cyberdeck'
    })}`
    // The callback must echo the challenge's hash; compare constantly.
    const expectedToken = createHash('sha256')
      .update(challenge.native_sso_token)
      .digest('hex')
      .slice(0, 16)

    return new Promise((resolve) => {
      const window = new BrowserWindow({
        width: 480,
        height: 720,
        title: 'Sign in to Meta',
        autoHideMenuBar: true,
        webPreferences: { nodeIntegration: false, contextIsolation: true }
      })

      let settled = false
      const finish = (result: { ok: boolean; name?: string; error?: string }): void => {
        if (settled) return
        settled = true
        window.destroy()
        resolve(result)
      }

      const completeLogin = async (callbackUrl: string): Promise<void> => {
        try {
          const url = new URL(callbackUrl)
          if (!['oculus:', 'oculus-client:'].includes(url.protocol)) return
          const token = url.searchParams.get('token') ?? ''
          const blob = url.searchParams.get('blob') ?? ''
          if (
            token.length !== expectedToken.length ||
            !timingSafeEqual(Buffer.from(token), Buffer.from(expectedToken))
          ) {
            throw new Error('Sign-in callback did not match this session.')
          }
          if (!blob) throw new Error('Invalid sign-in response.')
          const decrypted = await this.ssoPost('/webview_blobs_decrypt', {
            access_token: SSO_CLIENT,
            blob,
            request_token: challenge.native_sso_token!
          }).then((data) => data as { access_token?: string })
          if (!decrypted.access_token) throw new Error('Meta did not return a session token.')
          const profile = await this.ssoPost('/graphql', {
            access_token: decrypted.access_token,
            doc_id: '24112177345042346',
            variables: JSON.stringify({ app_id: SSO_PROFILE_APP_ID })
          }).then(
            (data) =>
              data as {
                data?: {
                  xfr_create_profile_token?: { profile_tokens?: { access_token?: string }[] }
                }
              }
          )
          const questToken =
            profile.data?.xfr_create_profile_token?.profile_tokens?.[0]?.access_token
          if (!questToken) throw new Error('Meta did not return a Quest account token.')
          finish(await this.setToken(questToken))
        } catch (error) {
          finish({ ok: false, error: error instanceof Error ? error.message : 'Sign-in failed' })
        }
      }

      // Custom-protocol navigations never load; intercept and consume them.
      const intercept = (event: Electron.Event, url: string): void => {
        if (!url.startsWith('oculus:') && !url.startsWith('oculus-client:')) return
        event.preventDefault()
        void completeLogin(url)
      }
      window.webContents.on('will-navigate', intercept)
      window.webContents.on('will-redirect', intercept)
      window.webContents.setWindowOpenHandler(({ url }) => {
        if (url.startsWith('oculus:') || url.startsWith('oculus-client:')) {
          void completeLogin(url)
          return { action: 'deny' }
        }
        return { action: 'allow' }
      })

      window.on('closed', () => {
        finish({ ok: false, error: 'Login window closed before finishing sign-in.' })
      })

      window.webContents.on('did-fail-load', (_event, code, description) => {
        if (!settled && code < 0) {
          finish({ ok: false, error: `Could not load the sign-in page: ${description}` })
        }
      })

      void window.loadURL(confirmUrl)
    })
  }

  /** POST to meta.graph.meta.com's plain JSON endpoints (never logged). */
  private async ssoPost(path: string, fields: Record<string, string>): Promise<unknown> {
    const response = await fetch(`${META_GRAPH_URI}${path}`, {
      method: 'POST',
      headers: { 'User-Agent': USER_AGENT },
      body: new URLSearchParams(fields)
    })
    if (!response.ok) {
      throw new Error(`Meta sign-in request failed (${response.status})`)
    }
    return response.json()
  }
}

export default new MetaAccountService()
