import { app, BrowserWindow, safeStorage } from 'electron'
import { promises as fs } from 'fs'
import { join } from 'path'
import { EventEmitter } from 'events'

const GRAPH_URI = 'https://graph.oculus.com/graphql'
const USER_AGENT = 'OculusGraphQLApiLib/1.0'

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
  const response = await fetch(GRAPH_URI, {
    method: 'POST',
    headers: { 'User-Agent': USER_AGENT },
    body
  })
  if (!response.ok) {
    throw new Error(`Meta request failed (${response.status})`)
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
   * In-app Meta login: the user signs in (2FA included) in an embedded browser
   * window; the app never sees the password, only the resulting `oc_ac_at`
   * session cookie, which Meta's own web client uses as the user token.
   */
  async loginWithBrowser(): Promise<{ ok: boolean; name?: string; error?: string }> {
    return new Promise((resolve) => {
      const partition = 'meta-login'
      const window = new BrowserWindow({
        width: 480,
        height: 720,
        title: 'Sign in to Meta',
        autoHideMenuBar: true,
        webPreferences: { partition, nodeIntegration: false, contextIsolation: true }
      })

      let settled = false
      const finish = (result: { ok: boolean; name?: string; error?: string }): void => {
        if (settled) return
        settled = true
        clearInterval(poll)
        window.destroy()
        resolve(result)
      }

      const poll = setInterval(async () => {
        if (window.isDestroyed()) return
        try {
          const cookies = await window.webContents.session.cookies.get({
            url: 'https://developer.oculus.com'
          })
          const token = cookies.find((cookie) => cookie.name === 'oc_ac_at')?.value
          if (token && isUserTokenShape(token)) {
            finish(await this.setToken(token))
          }
        } catch {
          /* keep polling while the login page is up */
        }
      }, 1500)

      window.on('closed', () => {
        clearInterval(poll)
        finish({ ok: false, error: 'Login window closed before finishing sign-in.' })
      })

      window.webContents.on('did-fail-load', (_event, code, description) => {
        if (!settled && code < 0) {
          finish({ ok: false, error: `Could not load the sign-in page: ${description}` })
        }
      })

      void window.loadURL('https://developer.oculus.com/manage')
    })
  }
}

export default new MetaAccountService()
