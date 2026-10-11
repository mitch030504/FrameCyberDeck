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
  const apps = [
    { appId: '111', displayName: 'Update Game' },
    { appId: '222', displayName: 'New Game' },
    { appId: '333', displayName: 'Up To Date' },
    { appId: '444', displayName: 'PC Only' }
  ]
  const versions = {
    '111': {
      versionCode: 20,
      version: '1.2',
      binaryId: 'b1',
      packageName: 'com.update.game'
    },
    '222': {
      versionCode: 5,
      version: '1.0',
      binaryId: 'b2',
      packageName: 'com.new.game'
    },
    '333': {
      versionCode: 7,
      version: '1.1',
      binaryId: 'b3',
      packageName: 'com.current.game'
    }
  }
  const catalog = [
    { packageName: 'com.update.game', versionCode: 10 },
    { packageName: 'com.current.game', versionCode: 7 }
  ]

  it('flags new titles and newer versions, skips up-to-date and non-Android', () => {
    const never = (): boolean => false
    const candidates = selectCandidates(apps, versions, catalog, never)
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

  it('skips versions already blacklisted (already contributed)', () => {
    const blacklist = (packageName: string, versionCode: number): boolean =>
      packageName === 'com.update.game' && versionCode === 20
    const candidates = selectCandidates(apps, versions, catalog, blacklist)
    expect(candidates.map((candidate) => candidate.packageName)).toEqual(['com.new.game'])
  })

  it('keeps an older owned version when the server is newer', () => {
    const candidates = selectCandidates(
      [{ appId: '555', displayName: 'Downgrade' }],
      {
        '555': {
          versionCode: 3,
          version: '0.9',
          binaryId: 'b5',
          packageName: 'com.newer.server'
        }
      },
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
