import { beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const paths = vi.hoisted(() => ({ root: '/tmp/meta-sync-test' }))
vi.mock('electron', () => ({
  app: { getPath: () => paths.root, isPackaged: true },
  BrowserWindow: { getAllWindows: () => [] },
  safeStorage: { isEncryptionAvailable: () => true }
}))
vi.mock('./metaAccountService', () => ({
  default: { getToken: vi.fn() },
  graphRequest: vi.fn()
}))
vi.mock('./gameService', () => ({ default: {} }))
vi.mock('./settingsService', () => ({
  default: { getDownloadPath: () => paths.root, on: vi.fn() }
}))
vi.mock('@shared/ipc-utils', () => ({ typedWebContentsSend: { send: vi.fn() } }))
vi.mock('./adbService', () => ({ default: {} }))
vi.mock('./dependencyService', () => ({ default: {} }))
vi.mock('./frame/frameDevkitService', () => ({ default: {} }))
vi.mock('./fileSafety', () => ({ childPath: (base: string) => base }))

import metaSyncService, { safeFileName, selectCandidates } from './metaSyncService'
import metaAccountService, { graphRequest } from './metaAccountService'
import uploadService from './uploadService'

beforeEach(async () => {
  vi.clearAllMocks()
  paths.root = await fs.mkdtemp(join(tmpdir(), 'meta-sync-test-'))
})

describe('selectCandidates', () => {
  const titles = [
    {
      appId: '111',
      displayName: 'Update Game',
      packageName: 'com.update.game',
      versionCode: 20,
      version: '1.2',
      binaryId: 'b1',
      grantReason: 'UNKNOWN'
    },
    {
      appId: '222',
      displayName: 'New Paid Game',
      packageName: 'com.new.game',
      versionCode: 5,
      version: '1.0',
      binaryId: 'b2',
      grantReason: 'PAID_OFFER'
    },
    {
      appId: '333',
      displayName: 'Up To Date',
      packageName: 'com.current.game',
      versionCode: 7,
      version: '1.1',
      binaryId: 'b3',
      grantReason: 'PAID_OFFER'
    },
    {
      appId: '666',
      displayName: 'New Free Game',
      packageName: 'com.free.game',
      versionCode: 2,
      version: '0.2',
      binaryId: 'b6',
      grantReason: 'NUX'
    }
  ]
  const catalog = [
    { packageName: 'com.update.game', versionCode: 10 },
    { packageName: 'com.current.game', versionCode: 7 }
  ]

  it('flags paid new titles and updates (any grant reason), skips free new titles', () => {
    const never = (): boolean => false
    const candidates = selectCandidates(titles, catalog, never)
    expect(candidates).toEqual([
      expect.objectContaining({
        packageName: 'com.update.game',
        kind: 'update',
        serverVersionCode: 10,
        versionCode: 20
      }),
      expect.objectContaining({ packageName: 'com.new.game', kind: 'new' })
    ])
  })

  it('treats subscription (Horizon+) grants as paid new titles', () => {
    const candidates = selectCandidates(
      [
        {
          appId: '777',
          displayName: 'Horizon Plus Game',
          packageName: 'com.hplus.game',
          versionCode: 3,
          version: '1.0',
          binaryId: 'b7',
          grantReason: 'SUBSCRIPTION'
        }
      ],
      [],
      (): boolean => false
    )
    expect(candidates.map((candidate) => candidate.kind)).toEqual(['new'])
  })

  it('skips versions already blacklisted (already contributed)', () => {
    const blacklist = (packageName: string, versionCode: number): boolean =>
      packageName === 'com.update.game' && versionCode === 20
    const candidates = selectCandidates(titles, catalog, blacklist)
    expect(candidates.map((candidate) => candidate.packageName)).toEqual(['com.new.game'])
  })

  it('skips paid-granted demos as new titles but offers updates for demos already on the server', () => {
    const demoTitles = [
      {
        appId: '888',
        displayName: 'Beat Saber - Demo',
        packageName: 'com.beatgames.beatsaber.demo',
        versionCode: 10,
        version: '1.0',
        binaryId: 'b8',
        grantReason: 'PAID_OFFER'
      },
      {
        appId: '999',
        displayName: 'Creed Demo',
        packageName: 'com.survios.CreedDemo',
        versionCode: 4,
        version: '0.9',
        binaryId: 'b9',
        grantReason: 'PAID_OFFER'
      },
      {
        appId: '121',
        displayName: 'Store Demo',
        packageName: 'com.store.demo',
        versionCode: 6,
        version: '1.1',
        binaryId: 'b12',
        grantReason: 'PAID_OFFER'
      }
    ]
    const never = (): boolean => false
    expect(selectCandidates(demoTitles, [], never)).toEqual([])

    const candidates = selectCandidates(
      demoTitles,
      [{ packageName: 'com.store.demo', versionCode: 5 }],
      never
    )
    expect(candidates).toEqual([
      expect.objectContaining({
        packageName: 'com.store.demo',
        kind: 'update',
        serverVersionCode: 5
      })
    ])
  })

  it('keeps an older owned version when the server is newer', () => {
    const candidates = selectCandidates(
      [
        {
          appId: '555',
          displayName: 'Downgrade',
          packageName: 'com.newer.server',
          versionCode: 3,
          version: '0.9',
          binaryId: 'b5',
          grantReason: 'PAID_OFFER'
        }
      ],
      [{ packageName: 'com.newer.server', versionCode: 4 }],
      (): boolean => false
    )
    expect(candidates).toEqual([])
  })
})

describe('uploadService.addMetaItemToQueue', () => {
  it('queues a fully-resolved local item with its provenance', async () => {
    const apk = join(paths.root, 'com.test.game.apk')
    await fs.writeFile(apk, 'apk')
    expect(
      uploadService.addMetaItemToQueue({
        packageName: 'com.test.game',
        gameName: 'Test',
        versionCode: 42,
        sourcePath: apk,
        sourceObbPaths: [join(paths.root, 'main.42.com.test.game.obb')]
      })
    ).toBe(true)
    const queued = uploadService.getQueue().find((item) => item.packageName === 'com.test.game')
    expect(queued).toMatchObject({
      deviceId: 'meta',
      versionCode: 42,
      isLocalUpload: true,
      sourcePath: apk,
      sourceObbPaths: [join(paths.root, 'main.42.com.test.game.obb')]
    })
  })
})

describe('safeFileName', () => {
  it('accepts real release file names and rejects unsafe ones', () => {
    expect(safeFileName('pakchunk2100-Android_ASTC.pak')).toBe('pakchunk2100-Android_ASTC.pak')
    expect(safeFileName('main.3148.com.beatgames.beatsaber.obb')).toBe(
      'main.3148.com.beatgames.beatsaber.obb'
    )
    expect(safeFileName('Sounds.obb')).toBe('Sounds.obb')
    expect(safeFileName('../../evil.obb')).toBeNull()
    expect(safeFileName('con')).toBeNull()
    expect(safeFileName('nul.obb')).toBeNull()
    expect(safeFileName('trailing.obb ')).toBeNull()
    expect(safeFileName('bad<name.obb')).toBeNull()
    expect(safeFileName('')).toBeNull()
  })
})

describe('metaSyncService.queueUpdates', () => {
  it('downloads data files under their release names and queues them all for upload', async () => {
    vi.mocked(metaAccountService.getToken).mockResolvedValue('OCmocktoken')
    const graphMock = graphRequest as unknown as ReturnType<typeof vi.fn>
    graphMock.mockResolvedValue({
      data: {
        node: {
          obb_binary: { id: 'o1', file_name: 'Sounds.obb' },
          asset_files: {
            nodes: [
              { id: 'a1', file_name: 'pakchunk2100-Android_ASTC.pak' },
              { id: 'a2', file_name: '../../evil.obb' },
              { id: 'a3', file_name: 'optional-dlc.pack', is_required: false },
              { id: 'a4', file_name: 'paid-dlc.pack', iap_item: { id: 'iap1' } }
            ]
          }
        }
      }
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: { get: (): null => null },
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data'))
            controller.close()
          }
        })
      }))
    )
    const addSpy = vi.spyOn(uploadService, 'addMetaItemToQueue').mockReturnValue(true)

    const queued = await metaSyncService.queueUpdates([
      {
        packageName: 'com.mydearest.brazenblaze',
        appId: '1',
        displayName: 'Brazen Blaze',
        versionCode: 42,
        version: '1.0',
        binaryId: 'b1',
        kind: 'new'
      }
    ])

    expect(queued).toBe(1)
    const stageDir = join(paths.root, 'uploads', 'meta', 'com.mydearest.brazenblaze', '42')
    expect((await fs.readdir(stageDir)).sort()).toEqual(
      [
        '.complete',
        'Sounds.obb',
        'com.mydearest.brazenblaze.apk',
        'evil.obb',
        'pakchunk2100-Android_ASTC.pak'
      ].sort()
    )
    expect(addSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        packageName: 'com.mydearest.brazenblaze',
        sourceObbPaths: [
          join(stageDir, 'Sounds.obb'),
          join(stageDir, 'evil.obb'),
          join(stageDir, 'pakchunk2100-Android_ASTC.pak')
        ]
      })
    )
  })

  it('falls back to the lean binary query and downloads APK + OBB only when the rich one fails', async () => {
    vi.mocked(metaAccountService.getToken).mockResolvedValue('OCmocktoken')
    const graphMock = graphRequest as unknown as ReturnType<typeof vi.fn>
    graphMock.mockRejectedValueOnce(new Error('Meta request failed (500)')).mockResolvedValue({
      data: {
        node: {
          obb_binary: { id: 'o1', file_name: 'main.3148.com.beatgames.beatsaber.obb' },
          asset_files: { nodes: [{ id: 'a1' }, { id: 'a2' }] }
        }
      }
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: { get: (): null => null },
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data'))
            controller.close()
          }
        })
      }))
    )
    const addSpy = vi.spyOn(uploadService, 'addMetaItemToQueue').mockReturnValue(true)

    const queued = await metaSyncService.queueUpdates([
      {
        packageName: 'com.beatgames.beatsaber',
        appId: '2',
        displayName: 'Beat Saber',
        versionCode: 3148,
        version: '1.42',
        binaryId: 'b2',
        kind: 'new'
      }
    ])

    expect(queued).toBe(1)
    const stageDir = join(paths.root, 'uploads', 'meta', 'com.beatgames.beatsaber', '3148')
    expect((await fs.readdir(stageDir)).sort()).toEqual(
      ['.complete', 'com.beatgames.beatsaber.apk', 'main.3148.com.beatgames.beatsaber.obb'].sort()
    )
    expect(addSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        packageName: 'com.beatgames.beatsaber',
        sourceObbPaths: [join(stageDir, 'main.3148.com.beatgames.beatsaber.obb')]
      })
    )
  })

  it('downloads owned add-on files along with the base release', async () => {
    vi.mocked(metaAccountService.getToken).mockResolvedValue('OCmocktoken')
    const graphMock = graphRequest as unknown as ReturnType<typeof vi.fn>
    graphMock
      .mockResolvedValueOnce({
        data: {
          node: {
            obb_binary: { id: 'o1', file_name: 'Sounds.obb' },
            asset_files: {
              nodes: [
                { id: 'f1', file_name: 'pack-one.obb', size: '4', iap_item: { id: 'dlc1' } },
                { id: 'f2', file_name: 'pack-two.obb', size: '4', iap_item: { id: 'dlc2' } }
              ]
            }
          }
        }
      })
      .mockResolvedValueOnce({
        data: {
          node: {
            latest_supported_binary: {
              firstIapItems: {
                edges: [
                  { node: { id: 'dlc1', is_viewer_entitled: true } },
                  { node: { id: 'dlc2', is_viewer_entitled: false } }
                ]
              }
            }
          }
        }
      })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: { get: (): null => null },
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data'))
            controller.close()
          }
        })
      }))
    )
    const addSpy = vi.spyOn(uploadService, 'addMetaItemToQueue').mockReturnValue(true)

    const queued = await metaSyncService.queueUpdates([
      {
        packageName: 'com.example.dlcgame',
        appId: '3',
        displayName: 'DLC Game',
        versionCode: 7,
        version: '1.0',
        binaryId: 'b3',
        kind: 'new'
      }
    ])

    expect(queued).toBe(1)
    const stageDir = join(paths.root, 'uploads', 'meta', 'com.example.dlcgame', '7')
    const staged = (await fs.readdir(stageDir)).sort()
    expect(staged).toContain('pack-one.obb')
    expect(staged).not.toContain('pack-two.obb')
    expect(addSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        packageName: 'com.example.dlcgame',
        sourceObbPaths: [join(stageDir, 'Sounds.obb'), join(stageDir, 'pack-one.obb')]
      })
    )
  })
})

describe('metaSyncService.downloadBinary', () => {
  it('resumes from the part file when the ETag matches', async () => {
    const destination = join(paths.root, 'resume.apk')
    const part = `${destination}.part`
    await fs.writeFile(part, 'data')
    await fs.writeFile(`${part}.json`, JSON.stringify({ size: 8, etag: '"abc"' }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 206,
        headers: {
          get: (header: string): string | null =>
            header === 'content-range' ? 'bytes 4-7/8' : header === 'etag' ? '"abc"' : null
        },
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('more'))
            controller.close()
          }
        })
      }))
    )

    await (
      metaSyncService as unknown as {
        downloadBinary: (
          id: string,
          destination: string,
          token: string,
          size: number
        ) => Promise<void>
      }
    ).downloadBinary('id', destination, 'OCmocktoken', 8)

    expect(await fs.readFile(destination, 'utf-8')).toBe('datamore')
    await expect(fs.access(part)).rejects.toThrow()
    await expect(fs.access(`${part}.json`)).rejects.toThrow()
  })

  it('restarts from scratch when the server ignores the range', async () => {
    const destination = join(paths.root, 'restart.apk')
    const part = `${destination}.part`
    await fs.writeFile(part, 'stale')
    await fs.writeFile(`${part}.json`, JSON.stringify({ size: 8, etag: '"old"' }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: {
          get: (header: string): string | null => (header === 'etag' ? '"new"' : null)
        },
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('fresh'))
            controller.close()
          }
        })
      }))
    )

    await (
      metaSyncService as unknown as {
        downloadBinary: (
          id: string,
          destination: string,
          token: string,
          size: number
        ) => Promise<void>
      }
    ).downloadBinary('id', destination, 'OCmocktoken', 5)

    expect(await fs.readFile(destination, 'utf-8')).toBe('fresh')
  })
})
