import { beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'fs'

const paths = vi.hoisted(() => ({ root: '/tmp/catalog-key-test' }))
vi.mock('electron', () => ({
  app: {
    getPath: () => paths.root,
    getVersion: () => '1.2.3'
  }
}))

import { CatalogKeyService } from './catalogKeyService'

const fetchMock = vi.fn()

beforeEach(async () => {
  await fs.mkdir(paths.root, { recursive: true })
  fetchMock.mockReset()
  global.fetch = fetchMock as unknown as typeof fetch
  vi.unstubAllEnvs()
  // The test host may export real credentials (CI does); start from none so
  // each test states exactly which credentials it needs.
  vi.stubEnv('VRSRC_CLIENT_ID', '')
  vi.stubEnv('VRSRC_BUILD_SECRET', '')
  vi.stubEnv('FRAME_CYBERDECK_CLIENT_ID', '')
  vi.stubEnv('FRAME_CYBERDECK_BUILD_SECRET', '')
})

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body
  } as Response
}

describe('CatalogKeyService', () => {
  it('does nothing without a client id and build secret', async () => {
    const service = new CatalogKeyService()
    expect(service.hasCredentials()).toBe(false)
    await expect(service.ensureKey()).resolves.toBe('')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('mints a key with the install id and app version', async () => {
    vi.stubEnv('VRSRC_CLIENT_ID', 'client-123')
    vi.stubEnv('VRSRC_BUILD_SECRET', 'build-secret')
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        apiKey: 'short-key',
        baseUri: 'https://go.srcdl1.xyz',
        refreshAfter: new Date(Date.now() + 3_600_000).toISOString(),
        expiresAt: new Date(Date.now() + 86_400_000).toISOString()
      })
    )

    const service = new CatalogKeyService()
    await expect(service.ensureKey()).resolves.toBe('short-key')
    expect(service.getBaseUri()).toBe('https://go.srcdl1.xyz')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/mirror/app-key')
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body).toMatchObject({
      client_id: 'client-123',
      build_secret: 'build-secret',
      app_version: '1.2.3'
    })
    expect(body.install_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })

  it('caches until refreshAfter and re-mints after force', async () => {
    vi.stubEnv('VRSRC_CLIENT_ID', 'client-123')
    vi.stubEnv('VRSRC_BUILD_SECRET', 'build-secret')
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, {
          apiKey: 'first-key',
          baseUri: 'https://go.srcdl1.xyz',
          refreshAfter: new Date(Date.now() + 3_600_000).toISOString(),
          expiresAt: new Date(Date.now() + 86_400_000).toISOString()
        })
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          apiKey: 'second-key',
          baseUri: 'https://go.srcdl1.xyz',
          refreshAfter: new Date(Date.now() + 3_600_000).toISOString(),
          expiresAt: new Date(Date.now() + 86_400_000).toISOString()
        })
      )

    const service = new CatalogKeyService()
    await expect(service.ensureKey()).resolves.toBe('first-key')
    await expect(service.ensureKey()).resolves.toBe('first-key')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    service.invalidate()
    await expect(service.ensureKey()).resolves.toBe('second-key')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('rejects a non-HTTPS grant', async () => {
    vi.stubEnv('VRSRC_CLIENT_ID', 'client-123')
    vi.stubEnv('VRSRC_BUILD_SECRET', 'build-secret')
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        apiKey: 'short-key',
        baseUri: 'http://go.srcdl1.xyz',
        refreshAfter: new Date(Date.now() + 3_600_000).toISOString(),
        expiresAt: new Date(Date.now() + 86_400_000).toISOString()
      })
    )
    const service = new CatalogKeyService()
    await expect(service.ensureKey()).rejects.toThrow(/HTTPS/)
  })

  it('rejects an invalid key shape', async () => {
    vi.stubEnv('VRSRC_CLIENT_ID', 'client-123')
    vi.stubEnv('VRSRC_BUILD_SECRET', 'build-secret')
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        apiKey: 'bad key',
        baseUri: 'https://go.srcdl1.xyz',
        refreshAfter: new Date(Date.now() + 3_600_000).toISOString(),
        expiresAt: new Date(Date.now() + 86_400_000).toISOString()
      })
    )
    const service = new CatalogKeyService()
    await expect(service.ensureKey()).rejects.toThrow(/invalid key/)
  })

  it('reports a rejected build secret', async () => {
    vi.stubEnv('VRSRC_CLIENT_ID', 'client-123')
    vi.stubEnv('VRSRC_BUILD_SECRET', 'retired')
    fetchMock.mockResolvedValue(jsonResponse(403, { reason: 'retired' }))

    const service = new CatalogKeyService()
    await expect(service.ensureKey()).rejects.toThrow(/retired/)
  })
})
