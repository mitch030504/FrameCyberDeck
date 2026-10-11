import { app, BrowserWindow } from 'electron'
import { promises as fs, existsSync } from 'fs'
import { join, basename, resolve } from 'path'
import { childPath } from './fileSafety'
import { execFile } from 'child_process'
import SevenZip from 'node-7z'
import { awaitSevenZipStream } from './sevenZipUtils'
import adbService from './adbService'
import frameDevkitService from './frame/frameDevkitService'
import frameConversionService from './frame/frameConversionService'
import dependencyService from './dependencyService'
import gameService from './gameService'
import { EventEmitter } from 'events'
import { debounce } from './download/utils'
import { QueueManager } from './download/queueManager'
import { DownloadProcessor } from './download/downloadProcessor'
import { ExtractionProcessor } from './download/extractionProcessor'
import { InstallationProcessor } from './download/installationProcessor'
import {
  DownloadAPI,
  GameInfo,
  DownloadItem,
  DownloadStatus,
  AddToQueueResult,
  DownloadStorageStatus
} from '@shared/types'
import settingsService from './settingsService'
import { typedWebContentsSend } from '@shared/ipc-utils'
import { inspectDownloadStorage } from './download/storageAvailability'

interface VrpConfig {
  baseUri?: string
  password?: string
}

class DownloadService extends EventEmitter implements DownloadAPI {
  private downloadsPath: string
  private isInitialized = false
  private activeCount = 0
  private activePipelines = new Map<string, Promise<void>>()
  private pendingResumes = new Set<string>()
  private debouncedEmitUpdate: () => void
  private queueManager: QueueManager
  private downloadProcessor: DownloadProcessor
  private extractionProcessor: ExtractionProcessor
  private installationProcessor: InstallationProcessor
  private adbService: typeof adbService
  private appSelectedDevice: string | null = null
  private appIsConnected: boolean = false
  private sideloadingDisabled: boolean = false
  private storageStatus: DownloadStorageStatus

  constructor() {
    super()
    const downloadPath = settingsService.getDownloadPath()
    settingsService.on('download-path-changed', (path) => {
      this.setDownloadPath(path)
    })
    this.downloadsPath = downloadPath
    this.storageStatus = {
      path: downloadPath,
      state: 'checking',
      error: null,
      code: null
    }

    this.queueManager = new QueueManager()
    this.adbService = adbService
    this.debouncedEmitUpdate = debounce(this.emitUpdate.bind(this), 300)
    this.downloadProcessor = new DownloadProcessor(this.queueManager, this.debouncedEmitUpdate)
    this.extractionProcessor = new ExtractionProcessor(this.queueManager, this.debouncedEmitUpdate)
    this.installationProcessor = new InstallationProcessor(
      this.queueManager,
      this.adbService,
      this.debouncedEmitUpdate
    )
  }

  setDownloadPath(path: string): void {
    const previousPath = this.downloadsPath
    this.downloadsPath = path
    if (this.isInitialized && previousPath !== path) {
      for (const item of this.queueManager.getQueue()) {
        if (item.status === 'Queued') {
          this.queueManager.updateItem(item.releaseName, {
            downloadPath: childPath(path, item.releaseName)
          })
        }
      }
      this.emitUpdate()
    }
    void this.refreshStorageStatus(true)
  }

  public flushQueue(): Promise<void> {
    return this.queueManager.saveQueue()
  }

  public getStorageStatus(): Promise<DownloadStorageStatus> {
    return Promise.resolve({ ...this.storageStatus })
  }

  public retryStorage(): Promise<DownloadStorageStatus> {
    return this.refreshStorageStatus(false)
  }

  private emitStorageStatus(): void {
    const mainWindow = BrowserWindow.getAllWindows()[0]
    if (mainWindow && !mainWindow.isDestroyed()) {
      typedWebContentsSend.send(mainWindow, 'download:storage-status-changed', {
        ...this.storageStatus
      })
    }
  }

  private async refreshStorageStatus(allowCreate: boolean): Promise<DownloadStorageStatus> {
    const path = this.downloadsPath
    this.storageStatus = { path, state: 'checking', error: null, code: null }
    this.emitStorageStatus()

    const status = await inspectDownloadStorage(path, { allowCreate })
    // Ignore a stale check if the user selected another path while it ran.
    if (this.downloadsPath !== path) return { ...this.storageStatus }

    this.storageStatus = status
    this.emitStorageStatus()

    if (status.state === 'available') {
      console.log(`[DownloadService] Download location available: ${path}`)
      if (this.isInitialized) this.processQueue()
    } else {
      console.warn(
        `[DownloadService] Download location unavailable: ${path}`,
        status.code ?? '',
        status.error ?? ''
      )
    }

    return { ...status }
  }

  setAppConnectionState(selectedDevice: string | null, isConnected: boolean): void {
    console.log(
      `[Service] App connection state updated - Device: ${selectedDevice}, Connected: ${isConnected}`
    )
    this.appSelectedDevice = selectedDevice
    this.appIsConnected = isConnected
  }

  setSideloadingDisabled(disabled: boolean): void {
    if (this.sideloadingDisabled !== disabled) {
      console.log(`[Service] Sideloading disabled flag updated: ${disabled}`)
    }
    this.sideloadingDisabled = disabled
  }

  /**
   * The auto-install path runs in main while the disable-sideloading toggle
   * lives in the renderer's localStorage. The renderer pushes the value over
   * IPC so the install pipeline can honor it; if the toggle is on we treat
   * the post-extraction state as the final state and never touch the device.
   */
  private getTargetDeviceForInstallation(): string | null {
    console.log(
      `[Service] Checking app connection state - Device: ${this.appSelectedDevice}, Connected: ${this.appIsConnected}`
    )

    // If the app is not connected to any device, don't install
    if (!this.appIsConnected || !this.appSelectedDevice) {
      console.log('[Service] App is not connected to any device, skipping installation')
      return null
    }

    // Return the app's selected device for installation
    console.log(
      `[Service] Using app's connected device for installation: ${this.appSelectedDevice}`
    )
    return this.appSelectedDevice
  }

  async initialize(vrpConfig: VrpConfig): Promise<void> {
    if (this.isInitialized) return
    console.log('Initializing DownloadService...')

    this.downloadProcessor.setVrpConfig(vrpConfig)
    this.extractionProcessor.setVrpConfig(vrpConfig)

    await this.queueManager.loadQueue()

    const changed = this.queueManager.updateAllItems(
      (item) =>
        item.status === 'Downloading' ||
        item.status === 'Extracting' ||
        item.status === 'Installing',
      {
        status: 'Queued',
        pid: undefined,
        progress: 0,
        extractProgress: undefined
      }
    )

    if (changed) {
      console.log(
        'Reset status for items from Downloading/Extracting/Installing to Queued after restart.'
      )
    }

    const defaultPath = join(app.getPath('userData'), 'downloads')
    await this.refreshStorageStatus(this.downloadsPath === defaultPath)

    this.isInitialized = true
    console.log(
      `DownloadService initialized${this.storageStatus.state === 'available' ? '.' : ' with downloads paused.'}`
    )
    this.emitUpdate()
    if (this.storageStatus.state === 'available') this.processQueue()
  }

  /**
   * Push updated server credentials into the download and extraction
   * processors after the service has already initialized. This lets a server
   * added or changed at runtime (via Settings) take effect immediately,
   * instead of only being read once at app startup — without this, downloads
   * fail with "Missing server configuration" until the app is restarted.
   */
  updateVrpConfig(vrpConfig: VrpConfig): void {
    this.downloadProcessor.setVrpConfig(vrpConfig)
    this.extractionProcessor.setVrpConfig(vrpConfig)
    console.log(
      '[DownloadService] Server config updated at runtime - baseUri:',
      !!vrpConfig.baseUri
    )
  }

  public getQueue(): Promise<DownloadItem[]> {
    return Promise.resolve(this.queueManager.getQueue())
  }

  /**
   * Inspect the destination folder for a release to figure out what state it
   * is in on disk, separate from the queue. Catches the case where a user
   * cleared the queue or downloaded the same release with a different tool.
   *
   * - 'absent'    : folder doesn't exist or is empty
   * - 'partial'   : has only rclone .partial files (resumable in-progress run)
   * - 'completed' : has at least one real (non-.partial) file
   */
  public async checkOnDiskCompletion(
    releaseName: string
  ): Promise<'absent' | 'partial' | 'completed'> {
    const folderPath = childPath(this.downloadsPath, releaseName)
    if (!existsSync(folderPath)) return 'absent'
    let entries: string[]
    try {
      entries = await fs.readdir(folderPath)
    } catch {
      return 'absent'
    }
    if (entries.length === 0) return 'absent'
    const hasReal = entries.some((name) => !name.endsWith('.partial'))
    return hasReal ? 'completed' : 'partial'
  }

  public addToQueue(game: GameInfo): Promise<AddToQueueResult> {
    if (!this.isInitialized) {
      console.error('DownloadService not initialized. Cannot add to queue.')
      return Promise.resolve('duplicate')
    }
    if (!game.releaseName) {
      console.error(`Cannot add game ${game.name} to queue: Missing releaseName.`)
      return Promise.resolve('duplicate')
    }
    if (this.storageStatus.state !== 'available') {
      console.warn(`Cannot add ${game.releaseName}: download location is unavailable.`)
      return Promise.resolve('storage-unavailable')
    }

    return this.addToQueueInternal(game)
  }

  /**
   * Called from the renderer once the user picks an action in the
   * "files already exist" prompt. Bypasses the on-disk check.
   */
  public async addToQueueResolveExisting(
    game: GameInfo,
    action: 'reinstall' | 'redownload'
  ): Promise<AddToQueueResult> {
    if (!this.isInitialized || !game.releaseName) return 'duplicate'
    childPath(this.downloadsPath, game.releaseName)
    if (this.storageStatus.state !== 'available') return 'storage-unavailable'
    if (action === 'reinstall') {
      this.importExistingAsCompleted(game)
      return 'imported'
    }
    // redownload: wipe the existing folder so rclone copies into a clean dest
    const folderPath = childPath(this.downloadsPath, game.releaseName)
    try {
      await fs.rm(folderPath, { recursive: true, force: true })
    } catch (err) {
      console.error(`[Service] Failed to wipe ${folderPath} before redownload:`, err)
    }
    return this.addToQueueInternal(game, { skipOnDiskCheck: true })
  }

  private importExistingAsCompleted(game: GameInfo): void {
    const folderPath = childPath(this.downloadsPath, game.releaseName)
    const existing = this.queueManager.findItem(game.releaseName)
    if (existing) {
      this.queueManager.updateItem(game.releaseName, {
        status: 'Completed',
        progress: 100,
        extractProgress: 100,
        downloadPath: folderPath,
        error: undefined,
        gameId: game.id,
        gameName: game.name,
        packageName: game.packageName,
        thumbnailPath: game.thumbnailPath,
        size: game.size
      })
    } else {
      this.queueManager.addItem({
        gameId: game.id,
        releaseName: game.releaseName,
        packageName: game.packageName,
        gameName: game.name,
        status: 'Completed',
        progress: 100,
        extractProgress: 100,
        addedDate: Date.now(),
        thumbnailPath: game.thumbnailPath,
        downloadPath: folderPath,
        size: game.size
      })
    }
    console.log(`Imported existing folder for ${game.releaseName} as Completed.`)
    this.emitUpdate()
  }

  private async addToQueueInternal(
    game: GameInfo,
    opts: { skipOnDiskCheck?: boolean } = {}
  ): Promise<AddToQueueResult> {
    childPath(this.downloadsPath, game.releaseName)
    if (this.storageStatus.state !== 'available') return 'storage-unavailable'
    const existing = this.queueManager.findItem(game.releaseName)

    if (existing) {
      if (existing.status === 'Completed') {
        console.log(`Game ${game.releaseName} already downloaded.`)
        return 'duplicate'
      } else if (existing.status !== 'Error' && existing.status !== 'Cancelled') {
        console.log(
          `Game ${game.releaseName} is already in the queue with status: ${existing.status}.`
        )
        return 'duplicate'
      }
      console.log(`Re-adding game ${game.releaseName} after previous ${existing.status}.`)
      this.queueManager.removeItem(game.releaseName)
    }

    // On-disk check: a previous tool / earlier run / queue clear may have
    // left a complete copy on disk that the queue doesn't know about. Apply
    // the user's "When download already exists" preference.
    if (!opts.skipOnDiskCheck) {
      const diskState = await this.checkOnDiskCompletion(game.releaseName)
      if (diskState === 'completed') {
        const action = settingsService.getExistingDownloadAction()
        if (action === 'reinstall') {
          this.importExistingAsCompleted(game)
          return 'imported'
        }
        if (action === 'redownload') {
          const folderPath = childPath(this.downloadsPath, game.releaseName)
          try {
            await fs.rm(folderPath, { recursive: true, force: true })
          } catch (err) {
            console.error(`[Service] Failed to wipe ${folderPath} before auto-redownload:`, err)
          }
          // fall through to normal queueing below
        } else {
          // 'ask' — let the renderer prompt the user. We don't add anything
          // to the queue yet; the renderer follows up with
          // addToQueueResolveExisting.
          return 'needs-prompt'
        }
      }
    }

    const newItem: DownloadItem = {
      gameId: game.id,
      releaseName: game.releaseName,
      packageName: game.packageName,
      gameName: game.name,
      status: 'Queued',
      progress: 0,
      addedDate: Date.now(),
      thumbnailPath: game.thumbnailPath,
      downloadPath: childPath(this.downloadsPath, game.releaseName),
      size: game.size
    }
    this.queueManager.addItem(newItem)
    console.log(`Added ${game.releaseName} to download queue.`)
    this.emitUpdate()
    this.processQueue()
    return 'added'
  }

  private cancelActiveItem(releaseName: string, item: DownloadItem): void {
    if (item.status === 'Downloading') {
      console.log(`[Service] Requesting cancel download for ${releaseName}`)
      this.downloadProcessor.cancelDownload(releaseName, 'Cancelled')
    } else if (item.status === 'Extracting') {
      console.log(`[Service] Requesting cancel extraction for ${releaseName}`)
      this.extractionProcessor.cancelExtraction(releaseName)
      const updated = this.queueManager.updateItem(releaseName, {
        status: 'Cancelled',
        extractProgress: 0,
        pid: undefined,
        error: undefined
      })
      if (updated) this.debouncedEmitUpdate()
    }
  }

  public async removeFromQueue(releaseName: string): Promise<void> {
    const item = this.queueManager.findItem(releaseName)
    if (!item) return
    this.cancelActiveItem(releaseName, item)
    if (!(await this.deleteDownloadedFiles(releaseName))) return
    const removed = this.queueManager.removeItem(releaseName)
    if (removed) {
      console.log(`[Service] Removed ${releaseName} from queue (status: ${item.status}).`)
      this.emitUpdate()
    }
  }

  public async moveToFront(releaseName: string): Promise<boolean> {
    const moved = this.queueManager.moveQueuedToFront(releaseName)
    if (moved) {
      console.log(`[Service] Bumped ${releaseName} to front of queue.`)
      this.emitUpdate()
    }
    return moved
  }

  public async moveQueuedUp(releaseName: string): Promise<boolean> {
    const moved = this.queueManager.moveQueuedUp(releaseName)
    if (moved) {
      console.log(`[Service] Moved ${releaseName} up in queue.`)
      this.emitUpdate()
    }
    return moved
  }

  public async moveQueuedDown(releaseName: string): Promise<boolean> {
    const moved = this.queueManager.moveQueuedDown(releaseName)
    if (moved) {
      console.log(`[Service] Moved ${releaseName} down in queue.`)
      this.emitUpdate()
    }
    return moved
  }

  public async removeFromQueueOnly(releaseName: string): Promise<void> {
    const item = this.queueManager.findItem(releaseName)
    if (!item) return
    this.cancelActiveItem(releaseName, item)
    const removed = this.queueManager.removeItem(releaseName)
    if (removed) {
      console.log(
        `[Service] Removed ${releaseName} from queue without deleting files (status: ${item.status}).`
      )
      this.emitUpdate()
    }
  }

  private async processQueue(): Promise<void> {
    if (this.storageStatus.state !== 'available') {
      console.log('[Service ProcessQueue] Downloads paused: configured location is unavailable')
      return
    }
    const maxConcurrent = settingsService.getMaxConcurrentDownloads()
    // Launch as many concurrent pipelines as allowed
    while (this.activeCount < maxConcurrent) {
      const nextItem = this.queueManager
        .getQueue()
        .find((item) => item.status === 'Queued' && !this.activePipelines.has(item.releaseName))
      if (!nextItem) {
        if (this.activeCount === 0) {
          console.log('[Service ProcessQueue] No queued items and no active operations')
        }
        return
      }

      // CRITICAL: Change status from 'Queued' IMMEDIATELY before the async pipeline starts,
      // so the next loop iteration of findNextQueuedItem() won't pick the same item again.
      this.queueManager.updateItem(nextItem.releaseName, {
        status: 'Downloading',
        progress: this.pendingResumes.has(nextItem.releaseName) ? nextItem.progress : 0
      })

      // Mark as active immediately so the next loop iteration won't pick it again
      this.activeCount++
      console.log(
        `[Service ProcessQueue] Processing: ${nextItem.releaseName} (active: ${this.activeCount}/${maxConcurrent})`
      )

      // Fire off the pipeline without awaiting — runs concurrently
      const resume = this.pendingResumes.delete(nextItem.releaseName)
      const pipeline = resume ? this.runResumePipeline(nextItem) : this.runPipeline(nextItem)
      this.activePipelines.set(nextItem.releaseName, pipeline)
      pipeline.finally(() => {
        this.activePipelines.delete(nextItem.releaseName)
        this.activeCount--
        console.log(
          `[Service ProcessQueue] Finished pipeline for ${nextItem.releaseName} (active: ${this.activeCount})`
        )
        // Try to fill the freed slot
        this.processQueue()
      })
    }
  }

  private async runPipeline(nextItem: DownloadItem): Promise<void> {
    const targetDeviceId = this.getTargetDeviceForInstallation()

    try {
      const downloadResult = await this.downloadProcessor.startDownload(nextItem)
      if (!downloadResult.success) {
        console.log(
          `[Service ProcessQueue] Download failed/cancelled for ${nextItem.releaseName}. Status: ${downloadResult.finalState?.status}`
        )
        return
      }
      const itemAfterDownload = downloadResult.finalState
      if (!itemAfterDownload) {
        console.log(
          `[Service ProcessQueue] Download successful but no final state for ${nextItem.releaseName}.`
        )
        return
      }
      if (!downloadResult.startExtraction) {
        console.log(
          `[Service ProcessQueue] Download successful but extraction flag not set for ${nextItem.releaseName}.`
        )
        return
      }

      console.log(
        `[Service ProcessQueue] Download successful for ${itemAfterDownload.releaseName}. Starting extraction...`
      )
      const extractionSuccess = await this.extractionProcessor.startExtraction(itemAfterDownload)
      if (!extractionSuccess) {
        console.log(
          `[Service ProcessQueue] Extraction failed or was cancelled for ${itemAfterDownload.releaseName}.`
        )
        return
      }
      const itemAfterExtraction = this.queueManager.findItem(itemAfterDownload.releaseName)
      if (!itemAfterExtraction || itemAfterExtraction.status !== 'Completed') {
        console.warn(
          `[Service ProcessQueue] Extraction reported success for ${itemAfterDownload.releaseName}, but item status is now ${itemAfterExtraction?.status}. Skipping installation.`
        )
        return
      }

      // Re-check connection state before installation (device might have disconnected during extraction).
      // On startup the queue pipeline can run before the renderer has connected
      // to a device, so wait briefly instead of skipping the installation.
      const finalTargetDeviceId = await this.waitForTargetDevice(15_000)
      if (!finalTargetDeviceId) {
        console.warn(
          `[Service ProcessQueue] Extraction successful for ${itemAfterExtraction.releaseName}, but no device is connected. Skipping installation.`
        )
        return
      }

      if (targetDeviceId && targetDeviceId !== finalTargetDeviceId) {
        console.warn(
          `[Service ProcessQueue] Target device changed during processing. Was: ${targetDeviceId}, Now: ${finalTargetDeviceId}. Skipping installation.`
        )
        return
      }

      if (this.sideloadingDisabled) {
        console.log(
          `[Service ProcessQueue] Sideloading disabled - leaving ${itemAfterExtraction.releaseName} in Completed state, skipping auto-install on ${finalTargetDeviceId}.`
        )
        return
      }

      if (itemAfterExtraction.manifestWarning) {
        console.warn(
          `[Service ProcessQueue] ${itemAfterExtraction.releaseName} completed with a manifest mismatch - leaving in Completed state and skipping auto-install so the user can choose to install anyway.`
        )
        return
      }

      console.log(
        `[Service ProcessQueue] Extraction successful for ${itemAfterExtraction.releaseName}. Queuing installation on ${finalTargetDeviceId}...`
      )
      const installStartTime = Date.now()
      const installationSuccess = await this.installItemToTarget(
        itemAfterExtraction,
        finalTargetDeviceId
      )
      const installDuration = ((Date.now() - installStartTime) / 1000).toFixed(1)
      if (installationSuccess) {
        console.log(
          `[Service ProcessQueue] Installation completed for ${itemAfterExtraction.releaseName} in ${installDuration}s`
        )
        // Emit event on successful installation
        this.emit('installation:success', finalTargetDeviceId)
      } else {
        console.error(
          `[Service ProcessQueue] Installation failed for ${itemAfterExtraction.releaseName} after ${installDuration}s`
        )
      }
    } catch (error) {
      console.error(
        `[Service ProcessQueue] UNEXPECTED error in main processing loop for ${nextItem.releaseName}:`,
        error
      )
      const currentItem = this.queueManager.findItem(nextItem.releaseName)
      this.updateItemStatus(
        nextItem.releaseName,
        'Error',
        currentItem?.progress ?? 0,
        'Unexpected processing error',
        undefined,
        undefined,
        currentItem?.extractProgress
      )
    }
  }

  /**
   * Bounded wait for a connected device: the renderer usually selects the
   * Frame a moment after startup, but the queue pipeline can already be done
   * by then, silently skipping installs of queued items.
   */
  private async waitForTargetDevice(timeoutMs: number): Promise<string | null> {
    const deadline = Date.now() + timeoutMs
    let deviceId: string | null = null
    while (Date.now() < deadline) {
      deviceId = this.getTargetDeviceForInstallation()
      if (deviceId) return deviceId
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
    return this.getTargetDeviceForInstallation()
  }

  // Resume pipeline: same as runPipeline but uses resumeDownload instead of startDownload
  private async runResumePipeline(nextItem: DownloadItem): Promise<void> {
    const targetDeviceId = this.getTargetDeviceForInstallation()

    try {
      const downloadResult = await this.downloadProcessor.resumeDownload(nextItem)
      if (!downloadResult.success) {
        console.log(
          `[Service ResumeQueue] Download failed/cancelled for ${nextItem.releaseName}. Status: ${downloadResult.finalState?.status}`
        )
        return
      }
      const itemAfterDownload = downloadResult.finalState
      if (!itemAfterDownload || !downloadResult.startExtraction) {
        console.log(
          `[Service ResumeQueue] Download done but extraction not needed for ${nextItem.releaseName}.`
        )
        return
      }

      console.log(
        `[Service ResumeQueue] Download successful for ${itemAfterDownload.releaseName}. Starting extraction...`
      )
      const extractionSuccess = await this.extractionProcessor.startExtraction(itemAfterDownload)
      if (!extractionSuccess) {
        console.log(
          `[Service ResumeQueue] Extraction failed or was cancelled for ${itemAfterDownload.releaseName}.`
        )
        return
      }
      const itemAfterExtraction = this.queueManager.findItem(itemAfterDownload.releaseName)
      if (!itemAfterExtraction || itemAfterExtraction.status !== 'Completed') {
        console.warn(
          `[Service ResumeQueue] Extraction reported success but status is ${itemAfterExtraction?.status}. Skipping installation.`
        )
        return
      }

      const finalTargetDeviceId = await this.waitForTargetDevice(15_000)
      if (!finalTargetDeviceId) {
        console.warn(
          `[Service ResumeQueue] No connected device after extraction for ${itemAfterExtraction.releaseName}. Skipping installation.`
        )
        return
      }

      if (targetDeviceId && targetDeviceId !== finalTargetDeviceId) {
        console.warn(
          `[Service ResumeQueue] Target device changed. Skipping installation for ${itemAfterExtraction.releaseName}.`
        )
        return
      }

      if (this.sideloadingDisabled) {
        console.log(
          `[Service ResumeQueue] Sideloading disabled - leaving ${itemAfterExtraction.releaseName} in Completed state.`
        )
        return
      }

      if (itemAfterExtraction.manifestWarning) {
        console.warn(
          `[Service ResumeQueue] ${itemAfterExtraction.releaseName} completed with a manifest mismatch - leaving in Completed state so the user can choose to install anyway.`
        )
        return
      }

      console.log(
        `[Service ResumeQueue] Starting installation for ${itemAfterExtraction.releaseName} on ${finalTargetDeviceId}...`
      )
      const installStartTime = Date.now()
      const installationSuccess = await this.installItemToTarget(
        itemAfterExtraction,
        finalTargetDeviceId
      )
      const installDuration = ((Date.now() - installStartTime) / 1000).toFixed(1)
      if (installationSuccess) {
        console.log(
          `[Service ResumeQueue] Installation completed for ${itemAfterExtraction.releaseName} in ${installDuration}s`
        )
        this.emit('installation:success', finalTargetDeviceId)
      } else {
        console.error(
          `[Service ResumeQueue] Installation failed for ${itemAfterExtraction.releaseName} after ${installDuration}s`
        )
      }
    } catch (error) {
      console.error(`[Service ResumeQueue] UNEXPECTED error for ${nextItem.releaseName}:`, error)
      const currentItem = this.queueManager.findItem(nextItem.releaseName)
      this.updateItemStatus(
        nextItem.releaseName,
        'Error',
        currentItem?.progress ?? 0,
        'Unexpected processing error',
        undefined,
        undefined,
        currentItem?.extractProgress
      )
    }
  }

  private async isSteamFrameDevice(deviceId: string): Promise<boolean> {
    const devices = await this.adbService.listDevices()
    return Boolean(
      devices.find((device) => device.id === deviceId && device.type === 'device')?.isSteamFrame
    )
  }

  private async installItemToTarget(
    item: DownloadItem,
    deviceId: string,
    onProgress?: (step: string, percent?: number) => void,
    frameOriginalSourcePath?: string
  ): Promise<boolean> {
    if (!(await this.isSteamFrameDevice(deviceId))) {
      return await this.installationProcessor.startInstallation(item, deviceId, onProgress)
    }

    if (!item.downloadPath) {
      throw new Error(`No extracted path is available for ${item.releaseName}`)
    }

    const tracked = Boolean(this.queueManager.findItem(item.releaseName))
    const report = (step: string, percent?: number): void => {
      onProgress?.(step, percent)
      if (tracked) {
        this.updateItemStatus(
          item.releaseName,
          'Installing',
          percent ?? 100,
          undefined,
          undefined,
          undefined,
          100
        )
      }
    }

    let conversionCleanup: (() => Promise<void>) | null = null
    try {
      report('Preparing Steam Frame conversion…', 0)
      const converted = await frameConversionService.convert(
        item.downloadPath,
        item.gameName || item.releaseName,
        report
      )
      conversionCleanup = converted.cleanup

      const success = await frameDevkitService.deploy(
        converted.directory,
        item.gameName || item.releaseName,
        report,
        undefined,
        converted.result.alreadyConverted
          ? undefined
          : (frameOriginalSourcePath ?? item.downloadPath),
        item.thumbnailPath
      )
      if (tracked) {
        this.updateItemStatus(
          item.releaseName,
          success ? 'Completed' : 'InstallError',
          100,
          success ? undefined : 'Steam Frame Devkit deployment failed',
          undefined,
          undefined,
          100
        )
      }
      return success
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (tracked) {
        this.updateItemStatus(
          item.releaseName,
          'InstallError',
          100,
          message.substring(0, 250),
          undefined,
          undefined,
          100
        )
      }
      throw error
    } finally {
      if (conversionCleanup) await conversionCleanup()
    }
  }

  private updateItemStatus(
    releaseName: string,
    status: DownloadStatus,
    progress: number,
    error?: string,
    speed?: string,
    eta?: string,
    extractProgress?: number
  ): void {
    const updates: Partial<DownloadItem> = {
      status,
      progress,
      error,
      speed,
      eta
    }
    if (extractProgress !== undefined) {
      updates.extractProgress = extractProgress
    } else if (
      status !== 'Extracting' &&
      status !== 'Completed' &&
      status !== 'Installing' &&
      status !== 'InstallError'
    ) {
      updates.extractProgress = undefined
    }
    if (status !== 'Downloading') {
      updates.speed = undefined
      updates.eta = undefined
    }
    if (status !== 'Downloading' && status !== 'Extracting' && status !== 'Installing') {
      updates.pid = undefined
    }
    if (status !== 'Error' && status !== 'InstallError') {
      updates.error = undefined
    }

    const updated = this.queueManager.updateItem(releaseName, updates)
    if (updated) {
      this.debouncedEmitUpdate()
    } else {
      console.warn(`[Service updateItemStatus] Failed update for non-existent item: ${releaseName}`)
    }
  }

  private emitUpdate(): void {
    const mainWindow = BrowserWindow.getAllWindows()[0]
    if (mainWindow && !mainWindow.isDestroyed()) {
      typedWebContentsSend.send(mainWindow, 'download:queue-updated', this.queueManager.getQueue())
    }
  }

  public cancelUserRequest(releaseName: string): Promise<void> {
    const item = this.queueManager.findItem(releaseName)
    if (!item) {
      console.warn(`[Service cancelUserRequest] Cannot cancel ${releaseName} - not found.`)
      return Promise.resolve()
    }

    console.log(
      `[Service cancelUserRequest] User requesting cancel for ${releaseName}, status: ${item.status}, active: ${this.activeCount}`
    )

    if (item.status === 'Downloading' || item.status === 'Queued') {
      this.downloadProcessor.cancelDownload(releaseName, 'Cancelled')
      // The pipeline's .finally() will decrement activeCount and call processQueue()
    } else if (item.status === 'Extracting') {
      this.extractionProcessor.cancelExtraction(releaseName)
      const updated = this.queueManager.updateItem(releaseName, {
        status: 'Cancelled',
        extractProgress: 0,
        pid: undefined,
        error: undefined
      })
      if (updated) this.debouncedEmitUpdate()
      // The pipeline's .finally() will decrement activeCount and call processQueue()
    } else if (item.status === 'Installing') {
      console.warn(
        `[Service cancelUserRequest] Cancellation requested for ${releaseName} during 'Installing' state - Not supported.`
      )
    } else {
      console.warn(
        `[Service cancelUserRequest] Cannot cancel ${releaseName} - status: ${item.status}`
      )
    }

    return Promise.resolve()
  }

  /**
   * Resolve the on-disk folder for a release. After a successful
   * download+extract the item's downloadPath already points at
   * <downloadsPath>/<releaseName>; before that it may still be the parent
   * downloads folder, so fall back to joining the release name.
   */
  private resolveReleaseFolder(item: DownloadItem): string {
    childPath(this.downloadsPath, item.releaseName)
    if (item.downloadPath && resolve(item.downloadPath) !== resolve(this.downloadsPath)) {
      if (basename(item.downloadPath) !== item.releaseName) {
        throw new Error('Download path does not identify the release folder')
      }
      return resolve(item.downloadPath)
    }
    return childPath(this.downloadsPath, item.releaseName)
  }

  public async retryDownload(releaseName: string): Promise<void> {
    const item = this.queueManager.findItem(releaseName)

    // Signature-mismatch (and other post-install) failures leave a fully
    // downloaded AND extracted payload sitting on disk — the archive parts are
    // already gone and the APK/OBB are ready to install. Re-running the whole
    // pipeline here would pointlessly re-download and re-extract everything
    // (the exact "it downloads again instead of seeing the files are already
    // there" complaint). Detect that case and jump straight to re-installing.
    if (item && item.status === 'InstallError') {
      const folderPath = this.resolveReleaseFolder(item)
      if (existsSync(folderPath) && (await this.folderContainsApk(folderPath))) {
        console.log(
          `[Service] Retry for ${releaseName}: extracted payload already on disk — skipping re-download, re-installing.`
        )
        this.queueManager.updateItem(releaseName, {
          status: 'Completed',
          progress: 100,
          extractProgress: 100,
          downloadPath: folderPath,
          error: undefined,
          pid: undefined,
          speed: undefined,
          eta: undefined
        })
        this.emitUpdate()

        const deviceId = this.getTargetDeviceForInstallation()
        if (deviceId && !this.sideloadingDisabled) {
          try {
            await this.installFromCompleted(releaseName, deviceId)
          } catch (err) {
            console.error(`[Service] Retry re-install failed for ${releaseName}:`, err)
          }
        } else {
          console.log(
            `[Service] Retry for ${releaseName}: left as Completed (no connected device or sideloading disabled).`
          )
        }
        return
      }
      console.log(
        `[Service] Retry for ${releaseName}: no extracted payload on disk — falling back to full re-download.`
      )
    }

    if (
      item &&
      (item.status === 'Cancelled' || item.status === 'Error' || item.status === 'InstallError')
    ) {
      console.log(`[Service] Retrying download: ${releaseName}`)

      if (this.downloadProcessor.isDownloadActive(releaseName)) {
        console.warn(
          `[Service Retry] Retrying item ${releaseName} with active download - cancelling first.`
        )
        this.downloadProcessor.cancelDownload(releaseName, 'Error', 'Cancelled before retry')
      }
      if (this.extractionProcessor.isExtractionActive(releaseName)) {
        console.warn(
          `[Service Retry] Retrying item ${releaseName} with active extraction - cancelling first.`
        )
        this.extractionProcessor.cancelExtraction(releaseName)
      }

      const updated = this.queueManager.updateItem(releaseName, {
        status: 'Queued',
        downloadPath: childPath(this.downloadsPath, releaseName),
        progress: 0,
        extractProgress: undefined,
        error: undefined,
        pid: undefined,
        speed: undefined,
        eta: undefined
      })
      if (updated) {
        this.emitUpdate()
        this.processQueue()
      } else {
        console.warn(`[Service Retry] Failed to update ${releaseName} for retry.`)
      }
    } else {
      console.warn(`[Service Retry] Cannot retry ${releaseName} - status: ${item?.status}`)
    }
    return Promise.resolve()
  }

  public pauseDownload(releaseName: string): void {
    const item = this.queueManager.findItem(releaseName)
    if (item?.status === 'Downloading') {
      this.downloadProcessor.pauseDownload(releaseName)
    }
  }

  public resumeDownload(releaseName: string): void {
    const item = this.queueManager.findItem(releaseName)
    if (!item || item.status !== 'Paused') return
    this.pendingResumes.add(releaseName)
    this.queueManager.updateItem(releaseName, { status: 'Queued', error: undefined })
    this.emitUpdate()
    void this.processQueue()
  }

  public async deleteDownloadedFiles(releaseName: string): Promise<boolean> {
    const item = this.queueManager.findItem(releaseName)
    if (!item) {
      console.warn(`Cannot delete files for ${releaseName}: Not found.`)
      return Promise.resolve(false)
    }

    if (item.status === 'Installing') return false
    this.cancelActiveItem(releaseName, item)
    await this.activePipelines.get(releaseName)

    let downloadPath: string
    try {
      downloadPath = this.resolveReleaseFolder(item)
    } catch (error) {
      this.queueManager.updateItem(releaseName, { error: String(error) })
      this.emitUpdate()
      return false
    }

    if (!existsSync(downloadPath)) {
      console.log(`Path not found for ${releaseName}: ${downloadPath}. Removing item.`)
      const removed = this.queueManager.removeItem(releaseName)
      if (removed) this.emitUpdate()
      return Promise.resolve(true)
    }

    console.log(`Deleting directory: ${downloadPath} for ${releaseName}...`)
    try {
      await fs.rm(downloadPath, { recursive: true, force: true })
      console.log(`Deleted directory ${downloadPath}.`)
      const removed = this.queueManager.removeItem(releaseName)
      if (removed) this.emitUpdate()
      return true
    } catch (error: unknown) {
      console.error(`Error deleting ${downloadPath} for ${releaseName}:`, error)
      let errorMsg = 'Failed to delete files.'
      if (error instanceof Error) {
        errorMsg = `Failed to delete files: ${error.message}`.substring(0, 200)
      } else {
        errorMsg = `Failed to delete files: ${String(error)}`.substring(0, 200)
      }
      const updated = this.queueManager.updateItem(releaseName, {
        error: errorMsg
      })
      if (updated) this.emitUpdate()
      return Promise.resolve(false)
    }
  }

  /**
   * Returns true if `folderPath` looks like a real Quest game payload.
   * Accepts: a top-level .apk, or one in any immediate subdirectory.
   * Skips deeper recursion to keep scans fast on large download folders.
   */
  private async folderContainsApk(folderPath: string): Promise<boolean> {
    try {
      const entries = await fs.readdir(folderPath, { withFileTypes: true })
      for (const entry of entries) {
        if (entry.isFile() && entry.name.toLowerCase().endsWith('.apk')) return true
      }
      for (const entry of entries) {
        if (!entry.isDirectory()) continue
        try {
          const inner = await fs.readdir(join(folderPath, entry.name), {
            withFileTypes: true
          })
          if (inner.some((e) => e.isFile() && e.name.toLowerCase().endsWith('.apk'))) return true
        } catch {
          // ignore unreadable subdirs
        }
      }
    } catch {
      return false
    }
    return false
  }

  private async findApkInFolder(folderPath: string): Promise<string | null> {
    try {
      const entries = await fs.readdir(folderPath, { withFileTypes: true })
      const top = entries.find((e) => e.isFile() && e.name.toLowerCase().endsWith('.apk'))
      if (top) return join(folderPath, top.name)
      for (const entry of entries) {
        if (!entry.isDirectory()) continue
        try {
          const inner = await fs.readdir(join(folderPath, entry.name), {
            withFileTypes: true
          })
          const nested = inner.find((e) => e.isFile() && e.name.toLowerCase().endsWith('.apk'))
          if (nested) return join(folderPath, entry.name, nested.name)
        } catch {
          /* ignore unreadable subdirs */
        }
      }
    } catch {
      /* ignore */
    }
    return null
  }

  private async extractPackageNameFromApk(apkPath: string): Promise<string> {
    const candidates: Array<{
      tool: string
      args: string[]
      parse: (out: string) => string
    }> = [
      {
        tool: 'aapt2',
        args: ['dump', 'packagename', apkPath],
        parse: (out) => out.trim().split('\n')[0].trim()
      },
      {
        tool: 'aapt',
        args: ['dump', 'badging', apkPath],
        parse: (out) => out.match(/^package: name='([^']+)'/m)?.[1] ?? ''
      }
    ]
    for (const { tool, args, parse } of candidates) {
      try {
        const stdout = await new Promise<string>((resolve, reject) => {
          execFile(tool, args, { timeout: 10000 }, (err, out) => (err ? reject(err) : resolve(out)))
        })
        const name = parse(stdout)
        if (name) {
          console.log(`[Service] Extracted packageName '${name}' via ${tool}`)
          return name
        }
      } catch {
        /* tool not available or failed — try next */
      }
    }
    return ''
  }

  private async inferPackageNameFromFolder(folderPath: string): Promise<string> {
    try {
      const contents = await fs.readdir(folderPath)
      if (!contents.some((f) => f.toLowerCase().endsWith('.apk'))) return ''
      const potentials = contents.filter((f) => f.includes('.') && !f.includes(' ') && f.length > 5)
      if (potentials.length === 1) return potentials[0]
    } catch {
      /* ignore */
    }
    return ''
  }

  private async resolvePackageName(
    dirName: string,
    folderPath: string,
    catalog: Map<string, string>
  ): Promise<string> {
    const fromCatalog = catalog.get(dirName)
    if (fromCatalog) return fromCatalog
    const apkPath = await this.findApkInFolder(folderPath)
    if (apkPath) {
      const fromApk = await this.extractPackageNameFromApk(apkPath)
      if (fromApk) return fromApk
    }
    return this.inferPackageNameFromFolder(folderPath)
  }

  public async scanDownloadFolder(): Promise<{ added: number; pruned: number }> {
    if (this.storageStatus.state !== 'available') {
      console.warn('[Service scanDownloadFolder] Skipped: download location is unavailable')
      return { added: 0, pruned: 0 }
    }
    let subdirs: string[] = []
    try {
      const dirents = await fs.readdir(this.downloadsPath, {
        withFileTypes: true
      })
      subdirs = dirents.filter((d) => d.isDirectory()).map((d) => d.name)
    } catch {
      return { added: 0, pruned: 0 }
    }

    // Pull the catalog so we can resolve packageName for re-imported entries.
    const catalogPackages = new Map<string, string>()
    try {
      const games = await gameService.getGames()
      for (const g of games) {
        if (g.releaseName && g.packageName) catalogPackages.set(g.releaseName, g.packageName)
      }
    } catch {
      // Catalog not loaded — we'll fall through to APK/folder detection only.
    }

    const queue = this.queueManager.getQueue()
    const queueMap = new Map(queue.map((item) => [item.releaseName, item]))
    let added = 0
    let pruned = 0
    let skipped = 0

    for (const dirName of subdirs) {
      const folderPath = join(this.downloadsPath, dirName)
      const existing = queueMap.get(dirName)

      // Already in the queue → leave it alone (or revive it below).
      if (!existing) {
        const matchesCatalog = catalogPackages.has(dirName)
        const hasApk = matchesCatalog ? true : await this.folderContainsApk(folderPath)
        if (!matchesCatalog && !hasApk) {
          skipped++
          continue
        }
        const packageName = await this.resolvePackageName(dirName, folderPath, catalogPackages)
        this.queueManager.addItem({
          gameId: dirName,
          releaseName: dirName,
          packageName,
          gameName: dirName,
          status: 'Completed',
          progress: 100,
          extractProgress: 100,
          addedDate: Date.now(),
          downloadPath: folderPath
        })
        added++
      } else if (
        existing.status === 'Cancelled' ||
        existing.status === 'Error' ||
        existing.status === 'InstallError'
      ) {
        const update: Partial<DownloadItem> = {
          status: 'Completed',
          progress: 100,
          extractProgress: 100,
          downloadPath: folderPath,
          error: undefined
        }
        if (!existing.packageName) {
          const pkg = await this.resolvePackageName(dirName, folderPath, catalogPackages)
          if (pkg) update.packageName = pkg
        }
        this.queueManager.updateItem(dirName, update)
        added++
      }
    }

    for (const item of queue) {
      if (
        item.status === 'Completed' ||
        item.status === 'Cancelled' ||
        item.status === 'Error' ||
        item.status === 'InstallError'
      ) {
        const folderPath = join(this.downloadsPath, item.releaseName)
        if (!existsSync(folderPath)) {
          this.queueManager.removeItem(item.releaseName)
          pruned++
        }
      }
    }

    if (added > 0 || pruned > 0) this.emitUpdate()
    console.log(`[Service scanDownloadFolder] added=${added} pruned=${pruned} skipped=${skipped}`)
    return { added, pruned }
  }

  public async installFromCompleted(releaseName: string, deviceId: string): Promise<void> {
    console.log(`[Service] Request to install completed item: ${releaseName} on ${deviceId}`)
    const item = this.queueManager.findItem(releaseName)

    if (!item) {
      console.error(`[Service installFromCompleted] Item not found: ${releaseName}`)
      throw new Error(`Item not found: ${releaseName}`)
    }

    // The user is explicitly choosing to install. If this item had a manifest
    // mismatch warning, clear it — they've accepted the risk and it shouldn't
    // linger on the item after installing.
    if (item.manifestWarning) {
      this.queueManager.updateItem(releaseName, { manifestWarning: undefined })
    }

    if (item.status !== 'Completed') {
      console.error(
        `[Service installFromCompleted] Item ${releaseName} has status ${item.status}, not 'Completed'. Cannot start installation.`
      )
      throw new Error(`Item ${releaseName} is not in 'Completed' state.`)
    }

    // Check if the app is connected to the target device
    const targetDeviceForInstall = this.getTargetDeviceForInstallation()
    if (!targetDeviceForInstall) {
      console.error(
        `[Service installFromCompleted] App is not connected to any device. Cannot install ${releaseName}.`
      )
      throw new Error('App is not connected to any device.')
    }

    if (targetDeviceForInstall !== deviceId) {
      console.error(
        `[Service installFromCompleted] App is connected to ${targetDeviceForInstall} but installation requested for ${deviceId}.`
      )
      throw new Error(`App is connected to a different device (${targetDeviceForInstall}).`)
    }

    // Check if the target device is still connected and authorized at the ADB level
    try {
      const devices = await this.adbService.listDevices()
      const targetDevice = devices.find((d) => d.id === deviceId && d.type === 'device')
      if (!targetDevice) {
        console.error(
          `[Service installFromCompleted] Target device ${deviceId} not found or not authorized at ADB level.`
        )
        throw new Error(`Target device ${deviceId} not found or not authorized.`)
      }
    } catch (err) {
      console.error(
        `[Service installFromCompleted] Error verifying target device ${deviceId}:`,
        err
      )
      throw new Error(`Failed to verify target device ${deviceId}.`)
    }

    if (item.status !== 'Completed') throw new Error('Installation is already queued')
    this.queueManager.updateItem(releaseName, { status: 'Installing' })
    this.emitUpdate()

    console.log(
      `[Service installFromCompleted] Triggering installation processor for ${releaseName} on ${deviceId}...`
    )

    // Directly trigger the installation processor
    // The installationProcessor will handle setting the status to 'Installing'
    try {
      const success = await this.installItemToTarget(item, deviceId)
      // Log based on success
      if (success) {
        console.log(
          `[Service installFromCompleted] Installation process initiated and reported success for ${releaseName}.`
        )
        // Emit event on successful installation
        this.emit('installation:success', deviceId)
      } else {
        throw new Error(this.queueManager.findItem(releaseName)?.error || 'Installation failed')
      }
    } catch (error) {
      console.error(
        `[Service installFromCompleted] Error initiating installation for ${releaseName}:`,
        error
      )
      // Attempt to set error status if possible
      this.updateItemStatus(
        releaseName,
        'InstallError',
        item.progress ?? 100, // Keep progress, default to 100 if undefined
        `Failed to start installation: ${error instanceof Error ? error.message : String(error)}`.substring(
          0,
          200
        ),
        undefined, // speed - not applicable
        undefined, // eta - not applicable
        item.extractProgress ?? 100 // Keep extract progress, default to 100 if undefined
      )
      // Re-throw or just log?
      throw error // Re-throw so the IPC handler logs it
    }
  }

  private async installSingleManualFolder(
    folderPath: string,
    deviceId: string,
    onProgress?: (step: string, percent?: number) => void
  ): Promise<boolean> {
    console.log(`[Service installManualFile] Installing folder: ${folderPath}`)

    const manualId = `manual-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`

    let packageName = ''
    try {
      const apkPath = await this.findApkInFolder(folderPath)
      if (apkPath) {
        packageName = await this.extractPackageNameFromApk(apkPath)
        if (!packageName) packageName = await this.inferPackageNameFromFolder(folderPath)
      }
    } catch (error) {
      console.log(`[Service installManualFile] Could not analyze folder structure: ${error}`)
    }

    const tempItem: DownloadItem = {
      gameId: manualId,
      releaseName: manualId,
      packageName: packageName,
      gameName: `Manual Install: ${folderPath.split(/[/\\]/).pop()}`,
      status: 'Completed',
      progress: 100,
      extractProgress: 100,
      addedDate: Date.now(),
      downloadPath: folderPath
    }

    const success = await this.installItemToTarget(tempItem, deviceId, onProgress)
    if (success) {
      console.log(`[Service installManualFile] Successfully installed folder: ${folderPath}`)
      this.emit('installation:success', deviceId)
    }
    return success
  }

  public async installManualFile(filePath: string, deviceId: string): Promise<boolean> {
    console.log(`[Service] Manual install requested for ${filePath} on device ${deviceId}`)

    // Report each install step (and its 0–100 percent) to the renderer so the
    // manual-install dialog shows live progress ("Installing game.apk… 40%")
    // instead of a static "Processing".
    const onProgress = (step: string, percent?: number): void => {
      this.emit('installation:progress', { step, percent })
    }
    onProgress('Starting…')

    // Check if the app is connected to the target device
    const targetDeviceForInstall = this.getTargetDeviceForInstallation()
    if (!targetDeviceForInstall) {
      console.error(
        `[Service installManualFile] App is not connected to any device. Cannot install ${filePath}.`
      )
      return false
    }

    if (targetDeviceForInstall !== deviceId) {
      console.error(
        `[Service installManualFile] App is connected to ${targetDeviceForInstall} but installation requested for ${deviceId}.`
      )
      return false
    }

    // Check if the target device is still connected and authorized at the ADB level
    try {
      const devices = await this.adbService.listDevices()
      const targetDevice = devices.find((d) => d.id === deviceId && d.type === 'device')
      if (!targetDevice) {
        console.error(
          `[Service installManualFile] Target device ${deviceId} not found or not authorized at ADB level.`
        )
        return false
      }
    } catch (err) {
      console.error(`[Service installManualFile] Error verifying target device ${deviceId}:`, err)
      return false
    }

    // Check if the file/folder exists
    if (!existsSync(filePath)) {
      console.error(`[Service installManualFile] File/folder not found: ${filePath}`)
      return false
    }

    try {
      const stats = await fs.stat(filePath)

      if (stats.isFile() && filePath.toLowerCase().endsWith('.apk')) {
        // Single APK file installation. Route through the installation processor
        // so that if an OBB folder sits alongside the chosen APK (i.e. the user
        // picked/dropped the APK from inside an extracted game folder) it gets
        // pushed too, instead of installing a data-less app.
        console.log(`[Service installManualFile] Installing single APK: ${filePath}`)
        let success: boolean
        if (await this.isSteamFrameDevice(deviceId)) {
          const converted = await frameConversionService.convert(
            filePath,
            basename(filePath, '.apk'),
            onProgress
          )
          try {
            success = await frameDevkitService.deploy(
              converted.directory,
              basename(filePath, '.apk'),
              onProgress,
              undefined,
              converted.result.alreadyConverted ? undefined : filePath
            )
          } finally {
            await converted.cleanup()
          }
        } else {
          success = await this.installationProcessor.installSingleApk(
            filePath,
            deviceId,
            onProgress
          )
        }
        if (success) {
          console.log(`[Service installManualFile] Successfully installed APK: ${filePath}`)
          this.emit('installation:success', deviceId)
        }
        return success
      } else if (stats.isDirectory()) {
        // Folder installation. The folder may either be a single game folder
        // (contains an APK or install.txt directly) or a parent folder
        // containing multiple game subfolders (batch install).
        console.log(`[Service installManualFile] Inspecting folder: ${filePath}`)

        const isGameFolder = async (dir: string): Promise<boolean> => {
          try {
            const entries = await fs.readdir(dir, { withFileTypes: true })
            return entries.some(
              (e) =>
                e.isFile() &&
                (e.name.toLowerCase().endsWith('.apk') || e.name.toLowerCase() === 'install.txt')
            )
          } catch {
            return false
          }
        }

        if (await isGameFolder(filePath)) {
          return await this.installSingleManualFolder(filePath, deviceId, onProgress)
        }

        // Not a game folder itself — look one level down for game subfolders
        const entries = await fs.readdir(filePath, { withFileTypes: true })
        const subfolders = entries.filter((e) => e.isDirectory()).map((e) => join(filePath, e.name))
        const gameSubfolders: string[] = []
        for (const sub of subfolders) {
          if (await isGameFolder(sub)) {
            gameSubfolders.push(sub)
          }
        }

        if (gameSubfolders.length === 0) {
          console.error(
            `[Service installManualFile] No APK/install.txt found in ${filePath} or its immediate subfolders.`
          )
          return false
        }

        console.log(
          `[Service installManualFile] Batch install: queuing ${gameSubfolders.length} game folder(s) from ${filePath}`
        )

        let successCount = 0
        let batchIndex = 0
        for (const sub of gameSubfolders) {
          batchIndex++
          const label = sub.split(/[/\\]/).pop() || sub
          const ok = await this.installSingleManualFolder(sub, deviceId, (step, percent) =>
            onProgress(`[${batchIndex}/${gameSubfolders.length}] ${label}: ${step}`, percent)
          )
          if (ok) successCount++
        }
        console.log(
          `[Service installManualFile] Batch install complete: ${successCount}/${gameSubfolders.length} succeeded`
        )
        return successCount === gameSubfolders.length
      } else if (stats.isFile() && filePath.toLowerCase().endsWith('.zip')) {
        // ZIP installation - extract to temp dir then run through installationProcessor
        // (which checks for install.txt and falls back to standard APK+OBB install)
        console.log(`[Service installManualFile] Installing from ZIP: ${filePath}`)

        const sevenZipPath = dependencyService.get7zPath()
        if (!sevenZipPath) {
          console.error('[Service installManualFile] 7zip not found, cannot extract ZIP')
          return false
        }

        const tmpDir = join(this.downloadsPath, `manual_install_${Date.now()}`)
        await fs.mkdir(tmpDir, { recursive: true })

        try {
          // Tolerate benign 7-Zip stderr noise (e.g. dylibs injected into the
          // 7zz child by macOS tweak frameworks); only a real ERROR rejects.
          onProgress('Extracting ZIP…')
          await awaitSevenZipStream(
            SevenZip.extractFull(filePath, tmpDir, { $bin: sevenZipPath }),
            (percent) => onProgress('Extracting ZIP…', percent)
          )

          const manualId = `manual-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`
          const tempItem: DownloadItem = {
            gameId: manualId,
            releaseName: manualId,
            packageName: '',
            gameName: `Manual Install: ${basename(filePath, '.zip')}`,
            status: 'Completed',
            progress: 100,
            extractProgress: 100,
            addedDate: Date.now(),
            downloadPath: tmpDir
          }

          const success = await this.installItemToTarget(tempItem, deviceId, onProgress, filePath)
          if (success) {
            console.log(`[Service installManualFile] Successfully installed from ZIP: ${filePath}`)
            this.emit('installation:success', deviceId)
          }
          return success
        } finally {
          await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
        }
      } else {
        console.error(`[Service installManualFile] Unsupported file type: ${filePath}`)
        return false
      }
    } catch (error) {
      console.error(
        `[Service installManualFile] Error during manual installation of ${filePath}:`,
        error
      )
      return false
    }
  }

  public async copyObbFolder(folderPath: string, deviceId: string): Promise<boolean> {
    console.log(`[Service] OBB folder copy requested for ${folderPath} on device ${deviceId}`)

    // Check if the app is connected to the target device
    const targetDeviceForInstall = this.getTargetDeviceForInstallation()
    if (!targetDeviceForInstall) {
      console.error(
        `[Service copyObbFolder] App is not connected to any device. Cannot copy OBB folder ${folderPath}.`
      )
      return false
    }

    if (targetDeviceForInstall !== deviceId) {
      console.error(
        `[Service copyObbFolder] App is connected to ${targetDeviceForInstall} but OBB copy requested for ${deviceId}.`
      )
      return false
    }

    // Check if the target device is still connected and authorized at the ADB level
    try {
      const devices = await this.adbService.listDevices()
      const targetDevice = devices.find((d) => d.id === deviceId && d.type === 'device')
      if (!targetDevice) {
        console.error(
          `[Service copyObbFolder] Target device ${deviceId} not found or not authorized at ADB level.`
        )
        return false
      }
    } catch (err) {
      console.error(`[Service copyObbFolder] Error verifying target device ${deviceId}:`, err)
      return false
    }

    if (await this.isSteamFrameDevice(deviceId)) {
      console.error(
        '[Service copyObbFolder] On Steam Frame, install the APK and OBB together so Devkit can upload game.apk + obb/*.obb.'
      )
      return false
    }

    // Check if the folder exists
    if (!existsSync(folderPath)) {
      console.error(`[Service copyObbFolder] Folder not found: ${folderPath}`)
      return false
    }

    try {
      const stats = await fs.stat(folderPath)
      if (!stats.isDirectory()) {
        console.error(`[Service copyObbFolder] Path is not a directory: ${folderPath}`)
        return false
      }

      // Get the folder name to use as the target directory name in OBB
      const folderName = folderPath.split(/[/\\]/).pop()
      if (!folderName) {
        console.error(
          `[Service copyObbFolder] Could not extract folder name from path: ${folderPath}`
        )
        return false
      }

      // Ensure the OBB base directory exists on the device
      const obbBasePath = '/sdcard/Android/obb'
      const targetObbPath = `${obbBasePath}/${folderName}`

      console.log(`[Service copyObbFolder] Creating OBB base directory: ${obbBasePath}`)
      try {
        await this.adbService.runShellCommand(deviceId, `mkdir -p "${obbBasePath}"`)
      } catch (mkdirError) {
        console.warn(
          `[Service copyObbFolder] Could not ensure OBB base directory exists (may already exist):`,
          mkdirError
        )
      }

      // Copy the entire folder to the OBB directory
      console.log(`[Service copyObbFolder] Copying folder ${folderPath} to ${targetObbPath}`)
      const success = await this.adbService.pushFileOrFolder(deviceId, folderPath, targetObbPath)

      if (success) {
        console.log(`[Service copyObbFolder] Successfully copied OBB folder to ${targetObbPath}`)
      } else {
        console.error(`[Service copyObbFolder] Failed to copy OBB folder to ${targetObbPath}`)
      }

      return success
    } catch (error) {
      console.error(`[Service copyObbFolder] Error during OBB folder copy of ${folderPath}:`, error)
      return false
    }
  }
}

export default new DownloadService()
