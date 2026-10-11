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
vi.mock('./metaAccountService', () => ({ default: { getToken: vi.fn() } }))
vi.mock('./gameService', () => ({ default: {} }))
vi.mock('./settingsService', () => ({
  default: { getDownloadPath: () => paths.root, on: vi.fn() }
}))
vi.mock('@shared/ipc-utils', () => ({ typedWebContentsSend: { send: vi.fn() } }))
vi.mock('./adbService', () => ({ default: {} }))
vi.mock('./dependencyService', () => ({ default: {} }))
vi.mock('./frame/frameDevkitService', () => ({ default: {} }))
vi.mock('./fileSafety', () => ({ childPath: (base: string) => base }))

import { selectCandidates } from './metaSyncService'
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
