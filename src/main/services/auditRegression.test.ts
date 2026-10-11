import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { EventEmitter } from 'node:events'
import type { DownloadItem, GameInfo, UploadItem } from '@shared/types'

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/frame-audit-tests', getAppPath: () => process.cwd() },
  BrowserWindow: { getAllWindows: () => [] }
}))
vi.mock('./settingsService', () => ({
  default: {
    on: vi.fn(),
    getDownloadPath: () => '/downloads',
    getMaxConcurrentDownloads: () => 2,
    getExistingDownloadAction: () => 'ask',
    getDownloadSpeedLimit: () => 0,
    getUploadSpeedLimit: () => 0
  }
}))
vi.mock('./adbService', () => ({
  SignatureMismatchError: class extends Error {},
  default: {
    installPackage: vi.fn().mockResolvedValue(false),
    pushFileOrFolder: vi.fn(),
    listDevices: vi.fn().mockResolvedValue([{ id: 'device', type: 'device' }]),
    runShellCommand: vi.fn(),
    pullFile: vi.fn()
  }
}))
vi.mock('./dependencyService', () => ({
  default: { getRclonePath: () => '/rclone', get7zPath: () => '/7z' }
}))
vi.mock('./gameService', () => ({ default: { isGameBlacklisted: () => false } }))
vi.mock('./logsService', () => ({ uploadTextToRentry: vi.fn() }))
vi.mock('./mirrorService', () => ({ default: {} }))
vi.mock('./frame/frameConversionService', () => ({ default: { convert: vi.fn() } }))
vi.mock('./frame/frameDevkitService', () => ({ default: { deploy: vi.fn() } }))
vi.mock('@shared/ipc-utils', () => ({ typedWebContentsSend: { send: vi.fn() } }))
vi.mock('execa', () => ({
  execa: vi.fn(() => Object.assign(Promise.resolve({}), { all: new EventEmitter() }))
}))

import downloads from './downloadService'
import uploads from './uploadService'
import backups from './backup/backupService'
import adb from './adbService'
import converter from './frame/frameConversionService'
import devkit from './frame/frameDevkitService'
import { QueueManager } from './download/queueManager'
import { InstallationProcessor } from './download/installationProcessor'
import { DownloadProcessor } from './download/downloadProcessor'
import { captureGameArchiveRouting } from './download/downloadProxy'
import { execa } from 'execa'

let root: string
let queue: QueueManager
const download = (overrides: Partial<DownloadItem> = {}): DownloadItem => ({
  gameId: 'id',
  gameName: 'Game',
  releaseName: 'release',
  packageName: 'com.test.game',
  status: 'Queued',
  progress: 0,
  addedDate: 1,
  downloadPath: join(root, 'downloads'),
  ...overrides
})
const upload = (overrides: Partial<UploadItem> = {}): UploadItem => ({
  packageName: 'com.test.game',
  gameName: 'Game',
  versionCode: 1,
  deviceId: 'local',
  status: 'Queued',
  progress: 0,
  addedDate: 1,
  ...overrides
})

beforeEach(async () => {
  root = await fs.mkdtemp(join(tmpdir(), 'frame-regression-'))
  queue = new QueueManager()
  queue['queuePath'] = join(root, 'queue.json')
  downloads['queueManager'] = queue
  downloads['downloadsPath'] = join(root, 'downloads')
  downloads['activeCount'] = 0
  downloads['isInitialized'] = true
  downloads['activePipelines'].clear()
  downloads['pendingResumes'].clear()
  downloads['storageStatus'] = {
    path: join(root, 'downloads'),
    state: 'available',
    error: null,
    code: null
  }
  uploads['uploadQueue'] = []
  uploads['status'] = 'INITIALIZED'
  uploads['uploadsBasePath'] = join(root, 'uploads')
  uploads['activePackage'] = null
  uploads['cancelled'].clear()
  uploads['isProcessing'] = false
  await fs.mkdir(join(root, 'downloads'))
})
afterEach(async () => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  await queue.saveQueue()
  await fs.rm(root, { recursive: true, force: true })
})

describe('download regressions', () => {
  it('deletes only the release folder for legacy queued entries', async () => {
    await fs.mkdir(join(root, 'downloads', 'release'))
    await fs.writeFile(join(root, 'downloads', 'keep.txt'), 'keep')
    queue.addItem(download())
    expect(await downloads.deleteDownloadedFiles('release')).toBe(true)
    expect(await fs.readFile(join(root, 'downloads', 'keep.txt'), 'utf8')).toBe('keep')
    expect(await fs.readdir(join(root, 'downloads'))).toEqual(['keep.txt'])
  })

  it('does not send catalog headers to mirrors or disable TLS checks', async () => {
    const item = download()
    queue.addItem(item)
    vi.stubEnv('RCLONE_HEADER', 'X-API-Key: test-secret')
    const processor = new DownloadProcessor(queue, () => {})
    processor.setVrpConfig({ baseUri: 'https://catalog.example/', password: 'test' })
    await processor['startRcloneCopyDownload'](
      item,
      captureGameArchiveRouting({ enabled: false, protocol: 'http', host: '', port: 8080 }),
      { configFilePath: '/mirror.config', remoteName: 'mirror' }
    )
    const call: unknown[] | undefined = vi.mocked(execa).mock.calls.at(-1)
    expect(call?.[1]).not.toContain('--no-check-certificate')
    const options = call?.[2] as { env: NodeJS.ProcessEnv; extendEnv: boolean }
    expect(options.extendEnv).toBe(false)
    expect(options.env.RCLONE_HEADER).toBeUndefined()
  })

  it('keeps valid persisted entries while disabling unsafe folder names', async () => {
    await fs.writeFile(
      queue.getQueuePath(),
      JSON.stringify([download(), download({ releaseName: '../private' })])
    )
    await queue.loadQueue()
    expect(queue.getQueue()).toHaveLength(2)
    expect(queue.findItem('release')?.status).toBe('Queued')
    expect(queue.findItem('../private')?.status).toBe('Error')
  })

  it('stores a per-release destination as soon as the item is queued', async () => {
    const original = downloads['processQueue']
    downloads['processQueue'] = vi.fn().mockResolvedValue(undefined)
    try {
      await downloads.addToQueue({
        id: 'id',
        name: 'Game',
        releaseName: 'release',
        packageName: 'com.test.game'
      } as GameInfo)
    } finally {
      downloads['processQueue'] = original
    }
    expect(queue.findItem('release')?.downloadPath).toBe(join(root, 'downloads', 'release'))
  })

  it('queues resume requests behind occupied slots and ignores duplicate requests', () => {
    queue.addItem(download({ status: 'Paused' }))
    downloads['activeCount'] = 2
    downloads.resumeDownload('release')
    downloads.resumeDownload('release')
    expect(queue.findItem('release')?.status).toBe('Queued')
    expect(downloads['activeCount']).toBe(2)
    expect(downloads['pendingResumes'].size).toBe(1)
  })

  it('waits for a paused pipeline to settle before starting its resume', async () => {
    queue.addItem(download())
    let finish: () => void = () => {}
    const pipeline = downloads['runPipeline']
    const resumePipeline = downloads['runResumePipeline']
    const resumes = vi.fn().mockResolvedValue(undefined)
    downloads['runPipeline'] = () =>
      new Promise((resolve) => {
        finish = resolve
      })
    downloads['runResumePipeline'] = resumes
    try {
      await downloads['processQueue']()
      queue.updateItem('release', { status: 'Paused', progress: 40 })
      downloads.resumeDownload('release')
      expect(downloads['activeCount']).toBe(1)
      expect(resumes).not.toHaveBeenCalled()
      finish()
      await vi.waitFor(() => expect(resumes).toHaveBeenCalledOnce())
      expect(queue.findItem('release')?.progress).toBe(40)
    } finally {
      downloads['runPipeline'] = pipeline
      downloads['runResumePipeline'] = resumePipeline
    }
  })

  it.each([true, false])(
    'records an original source only when this install converted it (alreadyConverted=%s)',
    async (alreadyConverted) => {
      const path = join(root, 'downloads', 'release')
      await fs.mkdir(path)
      const item = download({ status: 'Completed', downloadPath: path })
      queue.addItem(item)
      vi.mocked(converter.convert).mockResolvedValue({
        directory: path,
        cleanup: vi.fn().mockResolvedValue(undefined),
        result: {
          ok: true,
          converted: !alreadyConverted,
          alreadyConverted,
          gameApk: join(path, 'game.apk'),
          directory: path,
          obbCount: 0,
          sha256: 'hash'
        }
      })
      vi.mocked(devkit.deploy).mockResolvedValue(true)
      const isFrame = downloads['isSteamFrameDevice']
      downloads['isSteamFrameDevice'] = vi.fn().mockResolvedValue(true)
      try {
        expect(await downloads['installItemToTarget'](item, 'device')).toBe(true)
      } finally {
        downloads['isSteamFrameDevice'] = isFrame
      }
      expect(devkit.deploy).toHaveBeenCalledWith(
        path,
        'Game',
        expect.any(Function),
        undefined,
        alreadyConverted ? undefined : path,
        undefined
      )
    }
  )

  it('accepts a completed install even while every download slot is occupied', async () => {
    queue.addItem(download({ status: 'Completed' }))
    downloads['activeCount'] = 2
    downloads.setAppConnectionState('device', true)
    const installer = vi.fn().mockResolvedValue(true)
    const original = downloads['installItemToTarget']
    downloads['installItemToTarget'] = installer
    try {
      await downloads.installFromCompleted('release', 'device')
    } finally {
      downloads['installItemToTarget'] = original
    }
    expect(installer).toHaveBeenCalledOnce()
  })
})

describe('installation regressions', () => {
  it('treats a false APK install result as a failure', async () => {
    const path = join(root, 'downloads', 'release')
    await fs.mkdir(path)
    await fs.writeFile(join(path, 'game.apk'), '')
    const processor = new InstallationProcessor(queue, adb, () => {})
    expect(await processor.startInstallation(download({ downloadPath: path }), 'device')).toBe(
      false
    )
    expect(await processor.installSingleApk(join(path, 'game.apk'), 'device')).toBe(false)
  })

  it('rejects script pushes that escape the release', async () => {
    const path = join(root, 'downloads', 'release')
    await fs.mkdir(path)
    await fs.writeFile(join(root, 'private.txt'), 'secret')
    await fs.writeFile(join(path, 'install.txt'), 'adb push ../../private.txt /sdcard/private.txt')
    const processor = new InstallationProcessor(queue, adb, () => {})
    expect(await processor.startInstallation(download({ downloadPath: path }), 'device')).toBe(
      false
    )
    expect(adb.pushFileOrFolder).not.toHaveBeenCalled()
  })
})

describe('upload regressions', () => {
  it('rejects preparation paths that would delete the parent upload directory', async () => {
    await fs.writeFile(join(root, 'keep.txt'), 'keep')
    expect(await uploads.prepareUpload('..', 'Game', 1, 'device')).toBeNull()
    expect(await fs.readFile(join(root, 'keep.txt'), 'utf8')).toBe('keep')
  })

  it('keeps the original ZIP after uploading it', async () => {
    const path = join(root, 'original.zip')
    await fs.writeFile(path, 'original')
    uploads['configFilePath'] = path
    const item = upload({ isLocalUpload: true, sourcePath: path })
    uploads['uploadQueue'] = [item]
    expect(await uploads['processLocalUpload'](item)).toBe(true)
    expect(await fs.readFile(path, 'utf8')).toBe('original')
  })

  it('cancels preparation even before compression or rclone has started', () => {
    uploads['uploadQueue'] = [upload({ status: 'Preparing' })]
    uploads['activePackage'] = 'com.test.game'
    uploads.cancelUpload('com.test.game')
    expect(uploads.getQueue()[0].status).toBe('Cancelled')
    expect(() => uploads['assertNotCancelled']('com.test.game')).toThrow('Upload cancelled')
    uploads['updateItemStatus']('com.test.game', 'Completed')
    expect(uploads.getQueue()[0].status).toBe('Cancelled')
  })

  it('replaces an old failed local entry instead of creating duplicate package IDs', async () => {
    const path = join(root, 'game')
    await fs.mkdir(path)
    await fs.writeFile(join(path, 'game.apk'), '')
    uploads['uploadQueue'] = [upload({ status: 'Error' })]
    uploads['isProcessing'] = true
    const parse = uploads['getApkInfo']
    uploads['getApkInfo'] = vi
      .fn()
      .mockResolvedValue({ packageName: 'com.test.game', versionCode: 1 })
    try {
      await uploads.addLocalItemsToQueue([path])
    } finally {
      uploads['getApkInfo'] = parse
    }
    expect(uploads.getQueue()).toHaveLength(1)
    expect(uploads.getQueue()[0]).toMatchObject({
      status: 'Queued',
      isLocalUpload: true,
      sourcePath: path
    })
  })

  it('does not replace a cancelled local operation until it has settled', async () => {
    const path = join(root, 'game')
    await fs.mkdir(path)
    await fs.writeFile(join(path, 'game.apk'), '')
    uploads['uploadQueue'] = [upload({ status: 'Cancelled' })]
    uploads['activePackage'] = 'com.test.game'
    uploads['isProcessing'] = true
    const parse = uploads['getApkInfo']
    uploads['getApkInfo'] = vi
      .fn()
      .mockResolvedValue({ packageName: 'com.test.game', versionCode: 1 })
    try {
      await uploads.addLocalItemsToQueue([path])
    } finally {
      uploads['getApkInfo'] = parse
    }
    expect(uploads.getQueue()).toHaveLength(1)
    expect(uploads.getQueue()[0].status).toBe('Cancelled')
  })

  it('preserves local provenance when retrying', () => {
    uploads['uploadQueue'] = [
      upload({
        status: 'Error',
        isLocalUpload: true,
        sourcePath: '/original/game.apk',
        sourceObbPaths: ['/original/main.1.com.test.game.obb']
      })
    ]
    uploads['isProcessing'] = true
    expect(uploads.retryUpload('com.test.game')).toBe(true)
    expect(uploads.getQueue()[0]).toMatchObject({
      status: 'Queued',
      isLocalUpload: true,
      sourcePath: '/original/game.apk',
      sourceObbPaths: ['/original/main.1.com.test.game.obb']
    })
  })
})

it('rejects a backup ID that points at userData', async () => {
  expect(await backups.deleteBackup('..')).toBe(false)
  expect(await backups.deleteBackup('../other')).toBe(false)
})
