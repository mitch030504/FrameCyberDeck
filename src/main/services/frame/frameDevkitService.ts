import { writeJsonAtomic } from '../fileSafety'
import { app } from 'electron'
import { promises as fs } from 'fs'
import { basename, dirname, extname, join, resolve, sep } from 'path'
import { execa } from 'execa'
import type { PackageInfo } from '@shared/types'
import adbService from '../adbService'

export interface FrameDevkitStatus {
  ok: boolean
  host: string
  address?: string
  login?: string
  paired?: boolean
  pairError?: string | null
}

export interface FrameDeployResult {
  ok: boolean
  name: string
  directory: string
  startCommand: string
  runtime: 'Android'
  compatTool: 'fauxdroid'
  started: boolean
}

export interface FrameInstalledGame {
  gameid: string
  packageName: string
  versionCode: number
  versionName?: string
  title?: string
  applicationLabel?: string
  managed?: boolean
}

export interface FrameSourceRecord {
  packageName: string
  versionCode: number
  gameId: string
  originalObbPaths?: string[]
  originalSourcePath: string
  recordedAt: number
}

type ProgressReporter = (step: string, percent?: number) => void

class FrameDevkitService {
  private deployments: Promise<void> = Promise.resolve()
  private registryWrites: Promise<void> = Promise.resolve()
  private getSourceRegistryPath(): string {
    return join(app.getPath('userData'), 'frame-source-registry.json')
  }

  private async readSourceRegistry(): Promise<Record<string, FrameSourceRecord>> {
    try {
      const parsed = JSON.parse(await fs.readFile(this.getSourceRegistryPath(), 'utf-8')) as unknown
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
      return parsed as Record<string, FrameSourceRecord>
    } catch {
      return {}
    }
  }

  private async saveSourceRecord(record: FrameSourceRecord): Promise<void> {
    const write = this.registryWrites.then(async () => {
      const registry = await this.readSourceRegistry()
      registry[record.packageName] = record
      await writeJsonAtomic(this.getSourceRegistryPath(), registry)
    })
    this.registryWrites = write.catch(() => {})
    await write
    console.log(
      `[Frame Devkit] Recorded original source for ${record.packageName}: ${record.originalSourcePath}`
    )
  }

  async getOriginalSource(packageName: string): Promise<FrameSourceRecord | null> {
    const registry = await this.readSourceRegistry()
    const record = registry[packageName]
    if (!record?.originalSourcePath) return null
    try {
      await fs.access(record.originalSourcePath)
      return record
    } catch {
      console.warn(
        `[Frame Devkit] Original source for ${packageName} is no longer available: ${record.originalSourcePath}`
      )
      return null
    }
  }

  /**
   * Packages this app installed from a native Quest release — the only Frame
   * titles eligible for server contribution. Devkit games installed by other
   * tools are not Quest titles and stay off the server.
   */
  async getRecordedSourcePackages(): Promise<string[]> {
    const registry = await this.readSourceRegistry()
    return Object.values(registry)
      .filter((record) => Boolean(record?.originalSourcePath))
      .map((record) => record.packageName)
  }

  private getHelperPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'frame-devkit-bridge.py')
      : join(app.getAppPath(), 'resources', 'frame-devkit-bridge.py')
  }

  private async resolvePython(): Promise<string> {
    const candidates = [process.env.FRAME_CYBERDECK_PYTHON?.trim(), 'python3', 'python'].filter(
      (value): value is string => Boolean(value)
    )

    const failures: string[] = []
    for (const python of [...new Set(candidates)]) {
      try {
        await execa(python, ['--version'])
        return python
      } catch (error) {
        failures.push(`${python}: ${error instanceof Error ? error.message : String(error)}`)
      }
    }

    throw new Error(
      `Python 3 is required for Steam Frame Devkit deployment. ${failures.join(' | ')}`
    )
  }

  /**
   * Devkit endpoint forwarded over the Frame's USB ADB connection, so
   * deploys ride the USB cable instead of Wi-Fi. Fixed local ports keep the
   * forward list from growing; adb silently rebinds an existing forward.
   * Successful detections are sticky for five minutes: re-detecting per call
   * made a transient device-list blip fall back to hostname mid-pipeline.
   */
  private usbEndpointCache: {
    endpoint: { host: string; port: number; sshPort: number }
    at: number
  } | null = null

  private async usbDevkitEndpoint(): Promise<{
    host: string
    port: number
    sshPort: number
  } | null> {
    if (this.usbEndpointCache && Date.now() - this.usbEndpointCache.at < 300_000) {
      return this.usbEndpointCache.endpoint
    }
    try {
      const devices = await adbService.listDevices()
      // USB serials contain no ':' — network adb devices are 'host:port'.
      const frame = devices.find(
        (device) => device.isSteamFrame && device.type === 'device' && !device.id.includes(':')
      )
      if (!frame) {
        console.warn('[Frame Devkit] No USB-attached Steam Frame found; using hostname access.')
        return null
      }
      const httpPort = (
        await adbService.runLocalAdbCommand(`-s ${frame.id} forward tcp:43200 tcp:32000`)
      ).trim()
      const sshPort = (
        await adbService.runLocalAdbCommand(`-s ${frame.id} forward tcp:43222 tcp:22`)
      ).trim()
      if (!/^\d+$/.test(httpPort) || !/^\d+$/.test(sshPort)) {
        console.warn(
          `[Frame Devkit] ADB port forward failed (http="${httpPort}", ssh="${sshPort}"); using hostname access.`
        )
        return null
      }
      console.log(
        `[Frame Devkit] Using USB ADB forwards for ${frame.id}: devkit=:${httpPort}, ssh=:${sshPort}`
      )
      const endpoint = { host: '127.0.0.1', port: Number(httpPort), sshPort: Number(sshPort) }
      this.usbEndpointCache = { endpoint, at: Date.now() }
      return endpoint
    } catch (error) {
      console.warn(
        '[Frame Devkit] USB devkit endpoint unavailable, falling back to hostname:',
        error instanceof Error ? error.message : String(error)
      )
      return null
    }
  }

  private async bridge<T extends { ok: boolean; error?: string }>(
    command: string,
    args: string[] = [],
    host = process.env.FRAME_CYBERDECK_HOST?.trim() || 'frame',
    onDeployProgress?: (percent: number) => void
  ): Promise<T> {
    const python = await this.resolvePython()
    const helper = this.getHelperPath()

    // Prefer the USB cable when a Frame is attached over ADB; an explicit
    // FRAME_CYBERDECK_HOST override keeps direct hostname access.
    let bridgeHost = host
    let bridgeArgs: string[] = []
    if (!process.env.FRAME_CYBERDECK_HOST?.trim()) {
      const usb = await this.usbDevkitEndpoint()
      if (usb) {
        bridgeHost = usb.host
        bridgeArgs = ['--port', String(usb.port), '--ssh-port', String(usb.sshPort)]
      }
    }

    let stdout = ''
    let stderr = ''
    try {
      const child = execa(python, [helper, '--host', bridgeHost, ...bridgeArgs, command, ...args])
      // The bridge emits {"ok":true,"deployProgress":N} lines while rsync
      // runs; forward them for live progress. The final line is the result.
      if (onDeployProgress && child.stdout) {
        child.stdout.setEncoding('utf-8')
        let pending = ''
        child.stdout.on('data', (chunk: string) => {
          pending += chunk
          let newline = pending.indexOf('\n')
          while (newline >= 0) {
            const line = pending.slice(0, newline).trim()
            pending = pending.slice(newline + 1)
            if (line) {
              try {
                const parsed = JSON.parse(line) as { deployProgress?: number }
                if (typeof parsed.deployProgress === 'number') {
                  onDeployProgress(parsed.deployProgress)
                }
              } catch {
                // not a JSON progress line
              }
            }
            newline = pending.indexOf('\n')
          }
        })
      }
      const result = await child
      stdout = result.stdout
      stderr = result.stderr
    } catch (error) {
      const candidate = error as {
        stdout?: string
        stderr?: string
        shortMessage?: string
        message?: string
      }
      stdout = candidate.stdout ?? ''
      stderr = candidate.stderr ?? ''

      const failedLine = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)

      if (failedLine) {
        try {
          const parsed = JSON.parse(failedLine) as T
          if (!parsed.ok) {
            throw new Error(parsed.error || 'SteamOS Devkit operation failed')
          }
        } catch (parseError) {
          if (
            parseError instanceof Error &&
            parseError.message !== 'SteamOS Devkit operation failed' &&
            !parseError.message.startsWith('Unexpected token')
          ) {
            throw parseError
          }
        }
      }

      throw new Error(
        stderr.trim() ||
          candidate.shortMessage ||
          candidate.message ||
          'SteamOS Devkit bridge command failed'
      )
    }

    if (stderr.trim()) console.log(`[Frame Devkit] ${stderr.trim()}`)
    const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
    if (!line) throw new Error('SteamOS Devkit bridge returned no result')

    let parsed: T
    try {
      parsed = JSON.parse(line) as T
    } catch {
      throw new Error(`SteamOS Devkit bridge returned invalid JSON: ${line}`)
    }
    if (!parsed.ok) throw new Error(parsed.error || 'SteamOS Devkit operation failed')
    return parsed
  }

  async status(host?: string): Promise<FrameDevkitStatus> {
    return await this.bridge<FrameDevkitStatus>('status', [], host)
  }

  private async walkFiles(root: string, maxDepth: number): Promise<string[]> {
    if (maxDepth < 0) return []
    const files: string[] = []
    let entries
    try {
      entries = await fs.readdir(root, { withFileTypes: true })
    } catch {
      return []
    }

    for (const entry of entries) {
      const full = join(root, entry.name)
      if (entry.isFile()) files.push(full)
      else if (entry.isDirectory() && maxDepth > 0) {
        files.push(...(await this.walkFiles(full, maxDepth - 1)))
      }
    }
    return files
  }

  private async resolveAndroidPayload(
    sourcePath: string
  ): Promise<{ apk: string; obbs: string[]; root: string }> {
    const source = resolve(sourcePath)
    const stat = await fs.stat(source)
    let searchRoot: string
    let apkCandidates: string[]

    if (stat.isFile()) {
      if (!source.toLowerCase().endsWith('.apk')) {
        throw new Error(
          `Frame Devkit deploy currently accepts an APK or extracted game folder: ${source}`
        )
      }
      searchRoot = dirname(source)
      apkCandidates = [source]
    } else if (stat.isDirectory()) {
      searchRoot = source
      const files = await this.walkFiles(source, 2)
      apkCandidates = files.filter((file) => file.toLowerCase().endsWith('.apk'))
    } else {
      throw new Error(`Unsupported source path: ${source}`)
    }

    if (apkCandidates.length === 0) throw new Error('No APK was found in the selected game')

    const preferred =
      apkCandidates.find((file) => basename(file).toLowerCase() === 'game.apk') ??
      apkCandidates.find((file) => dirname(file) === searchRoot)

    if (!preferred && apkCandidates.length > 1) {
      throw new Error(
        `Multiple APKs were found (${apkCandidates.map((file) => basename(file)).join(', ')}). Split/multi-APK games are not supported by the Frame POC yet.`
      )
    }

    const apk = preferred ?? apkCandidates[0]
    const allFiles = await this.walkFiles(searchRoot, 4)
    // Game data: everything under an obb/ directory (converter output holds
    // non-OBB data there too, e.g. raw Unity bundles) plus classic .obb files.
    const obbs = allFiles.filter(
      (file) =>
        file.toLowerCase().endsWith('.obb') ||
        file.split(sep).some((part) => part.toLowerCase() === 'obb')
    )
    return { apk, obbs, root: searchRoot }
  }

  /** Path of `file` relative to its top-level obb/ directory (flat name otherwise). */
  private obbRelativePath(file: string): string {
    const parts = file.split(sep)
    let index = -1
    for (let i = 0; i < parts.length; i++) {
      if (parts[i].toLowerCase() === 'obb') index = i
    }
    return index === -1 ? basename(file) : parts.slice(index + 1).join(sep)
  }

  private async readConversionMetadata(root: string): Promise<{
    packageName?: string
    versionCode?: number
    versionName?: string
    applicationLabel?: string
    settings?: Record<string, string>
  }> {
    const path = join(root, 'frame-conversion.json')
    try {
      const parsed = JSON.parse(await fs.readFile(path, 'utf-8')) as {
        package?: unknown
        packageName?: unknown
        versionCode?: unknown
        versionName?: unknown
        applicationLabel?: unknown
        settings?: unknown
      }
      const packageName =
        typeof parsed.packageName === 'string'
          ? parsed.packageName
          : typeof parsed.package === 'string'
            ? parsed.package
            : undefined
      const versionCode =
        typeof parsed.versionCode === 'number' && Number.isFinite(parsed.versionCode)
          ? Math.trunc(parsed.versionCode)
          : undefined
      const versionName = typeof parsed.versionName === 'string' ? parsed.versionName : undefined
      const applicationLabel =
        typeof parsed.applicationLabel === 'string' ? parsed.applicationLabel : undefined
      const settings: Record<string, string> = {}
      if (parsed.settings && typeof parsed.settings === 'object') {
        for (const [key, value] of Object.entries(parsed.settings as Record<string, unknown>)) {
          if (typeof value === 'string' || typeof value === 'number') {
            settings[key] = String(value)
          }
        }
      }
      return { packageName, versionCode, versionName, applicationLabel, settings }
    } catch {
      return {}
    }
  }

  private async createStagingDirectory(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter,
    thumbnailPath?: string
  ): Promise<{
    directory: string
    devkitName: string
    packageName: string
    versionCode: number
  }> {
    onProgress?.('Analyzing Android package…', 5)
    const payload = await this.resolveAndroidPayload(sourcePath)
    const conversion = await this.readConversionMetadata(payload.root)
    const packageName = conversion.packageName
    if (!packageName || !/^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$/.test(packageName)) {
      throw new Error(
        'Converted APK has no valid package identity. Convert the original Quest APK again.'
      )
    }
    const devkitName = packageName
    const versionCode = conversion.versionCode ?? 0
    // Stage on the real disk, not the OS temp dir: /tmp is a small tmpfs on
    // many Linux distros, and a large game's OBBs overflow it mid-copy.
    const base = join(app.getPath('userData'), 'frame-cyberdeck')
    await fs.mkdir(base, { recursive: true })
    const directory = await fs.mkdtemp(join(base, `${devkitName}-`))

    try {
      onProgress?.('Preparing Frame title…', 15)
      await fs.copyFile(payload.apk, join(directory, 'game.apk'))

      // Library artwork for the Steam shortcut; the devkit-side polish step
      // installs it as grid/<appid>p/_hero on the Frame.
      if (thumbnailPath) {
        try {
          await fs.copyFile(thumbnailPath, join(directory, `library${extname(thumbnailPath)}`))
        } catch (error) {
          console.warn(
            '[Frame Devkit] Could not stage library artwork:',
            error instanceof Error ? error.message : String(error)
          )
        }
      }

      await fs.writeFile(
        join(directory, 'frame-cyberdeck.json'),
        JSON.stringify(
          {
            schemaVersion: 1,
            gameId: devkitName,
            // The caller's game name (the catalog title) is the name the user
            // knows; the APK's own label is often an internal codename.
            title: title || conversion.applicationLabel || devkitName,
            packageName,
            versionCode,
            versionName: conversion.versionName ?? '',
            applicationLabel: title || conversion.applicationLabel || devkitName
          },
          null,
          2
        ),
        'utf-8'
      )

      // The FrameBridge adapter reads this through LEPTON_ENV_FRAMEBRIDGE_CONFIG
      // (set by the game's launch.sh), so settings can change without a
      // reconversion. Mirrors the APK's baked libframe_settings.so.
      if (conversion.settings && Object.keys(conversion.settings).length > 0) {
        const conf = Object.entries(conversion.settings)
          .map(([key, value]) => `${key}=${value}`)
          .join('\n')
        await fs.writeFile(join(directory, 'settings.conf'), `${conf}\n`, 'utf-8')
      }

      if (payload.obbs.length > 0) {
        const obbDir = join(directory, 'obb')
        await fs.mkdir(obbDir, { recursive: true })
        const names = new Set<string>()
        let copied = 0
        for (const obb of payload.obbs) {
          const name = this.obbRelativePath(obb)
          if (names.has(name)) {
            throw new Error(
              `Two OBB files have the same filename (${name}); refusing an ambiguous upload.`
            )
          }
          names.add(name)
          await fs.mkdir(join(obbDir, dirname(name)), { recursive: true })
          await fs.copyFile(obb, join(obbDir, name))
          copied++
          onProgress?.(
            `Preparing OBB data (${copied}/${payload.obbs.length})…`,
            15 + Math.round((copied / payload.obbs.length) * 20)
          )
        }
      }

      return { directory, devkitName, packageName, versionCode }
    } catch (error) {
      await fs.rm(directory, { recursive: true, force: true }).catch(() => {})
      throw error
    }
  }

  async listGames(host?: string): Promise<FrameInstalledGame[]> {
    const result = await this.bridge<{
      ok: boolean
      error?: string
      games: FrameInstalledGame[]
    }>('list', [], host)
    return result.games
  }

  private selectGame(
    games: FrameInstalledGame[],
    packageName: string
  ): FrameInstalledGame | undefined {
    const exactGameId = games.find((entry) => entry.gameid === packageName)
    if (exactGameId) return exactGameId

    return games
      .filter((entry) => entry.packageName === packageName)
      .sort((a, b) => {
        if (Boolean(a.managed) !== Boolean(b.managed)) return a.managed ? -1 : 1
        return (b.versionCode || 0) - (a.versionCode || 0)
      })[0]
  }

  async getInstalledPackages(host?: string): Promise<PackageInfo[]> {
    const games = await this.listGames(host)
    const bestByPackage = new Map<string, FrameInstalledGame>()

    for (const game of games) {
      const packageName = game.packageName || game.gameid
      const current = bestByPackage.get(packageName)
      if (!current) {
        bestByPackage.set(packageName, game)
        continue
      }

      const preferred = this.selectGame([current, game], packageName)
      if (preferred) bestByPackage.set(packageName, preferred)
    }

    return [...bestByPackage.entries()].map(([packageName, game]) => ({
      packageName,
      versionCode: Number.isFinite(game.versionCode) ? game.versionCode : 0,
      applicationLabel: game.applicationLabel || game.title || packageName
    }))
  }

  async uninstallPackage(packageName: string, host?: string): Promise<boolean> {
    const games = await this.listGames(host)

    const exactGameId = games.find((entry) => entry.gameid === packageName)
    const matches = exactGameId
      ? [exactGameId]
      : games.filter((entry) => entry.packageName === packageName)

    if (matches.length === 0) {
      console.warn(`[Frame Devkit] No installed title found for ${packageName}`)
      return false
    }

    // Multiple Devkit shortcuts can point at the same Android package (for
    // example an old smoke-test title plus a newer CyberDeck-managed title).
    // Removing the Android package should clear every shortcut representing it,
    // otherwise the UI immediately rediscovers the same package as installed.
    const ordered = [...matches].sort((a, b) => {
      if (Boolean(a.managed) !== Boolean(b.managed)) return a.managed ? -1 : 1
      return (b.versionCode || 0) - (a.versionCode || 0)
    })

    for (const game of ordered) {
      console.log(
        `[Frame Devkit] Removing ${game.gameid} for package ${packageName}${game.managed ? ' (managed)' : ''}`
      )
      await this.bridge<{ ok: boolean; error?: string }>('delete', ['--name', game.gameid], host)
    }
    return true
  }

  async runGame(gameId: string, host?: string): Promise<boolean> {
    console.log(`[Frame Devkit] Launching Devkit title ${gameId}`)
    await this.bridge<{ ok: boolean; error?: string }>('run', ['--name', gameId], host)
    console.log(`[Frame Devkit] Launch request accepted for ${gameId}`)
    return true
  }

  async runPackage(packageName: string, host?: string): Promise<boolean> {
    const games = await this.listGames(host)
    const game = this.selectGame(games, packageName)
    if (!game) {
      console.warn(`[Frame Devkit] No installed title found for ${packageName}`)
      return false
    }
    console.log(
      `[Frame Devkit] Resolved package ${packageName} to ${game.gameid}${game.managed ? ' (managed)' : ''}`
    )
    return await this.runGame(game.gameid, host)
  }

  deploy(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter,
    host?: string,
    originalSourcePath?: string,
    thumbnailPath?: string
  ): Promise<boolean> {
    const deployment = this.deployments.then(() =>
      this.deployNow(sourcePath, title, onProgress, host, originalSourcePath, thumbnailPath)
    )
    this.deployments = deployment.then(
      () => {},
      () => {}
    )
    return deployment
  }

  private async deployNow(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter,
    host?: string,
    originalSourcePath?: string,
    thumbnailPath?: string
  ): Promise<boolean> {
    let staging: string | null = null
    try {
      const status = await this.status(host)
      if (!status.paired) {
        throw new Error(
          `Steam Frame ${status.address || status.host} is visible but not paired with SteamOS Devkit Client. On Frame choose Settings > Developer > Pair new host, then register it in SteamOS Devkit Client.`
        )
      }

      const prepared = await this.createStagingDirectory(
        sourcePath,
        title,
        onProgress,
        thumbnailPath
      )
      staging = prepared.directory
      const installed = await this.listGames(host)
      const previous = installed.find(
        (game) =>
          game.managed &&
          game.packageName === prepared.packageName &&
          game.gameid !== '.' &&
          game.gameid !== '..' &&
          /^[A-Za-z0-9._]+$/.test(game.gameid)
      )
      if (previous && previous.gameid !== prepared.devkitName) {
        // Keep existing Steam shortcut settings when upgrading a verified legacy install.
        prepared.devkitName = previous.gameid
        const metadataPath = join(staging, 'frame-cyberdeck.json')
        const metadata = JSON.parse(await fs.readFile(metadataPath, 'utf-8'))
        await writeJsonAtomic(metadataPath, { ...metadata, gameId: prepared.devkitName })
      }
      const occupied = installed.find((game) => game.gameid === prepared.devkitName)
      // An unmanaged title with the same package is the same game (for
      // example an older hand-registered install) — updating it is fine.
      // A different package holding this ID would lose its data; refuse.
      if (occupied && occupied.packageName !== prepared.packageName) {
        throw new Error(
          `Devkit ID ${prepared.devkitName} is already used by another title; refusing to overwrite it.`
        )
      }
      onProgress?.('Uploading to Steam Frame with SteamOS Devkit…', 40)

      const result = await this.bridge<FrameDeployResult>(
        'deploy',
        [
          '--name',
          prepared.devkitName,
          '--directory',
          prepared.directory,
          '--start-command',
          'launch.sh'
        ],
        host,
        (percent) =>
          onProgress?.(
            'Uploading to Steam Frame with SteamOS Devkit…',
            40 + Math.round(percent * 0.6)
          )
      )

      console.log(
        `[Frame Devkit] Deployed ${result.name}: ${result.startCommand}, runtime=${result.runtime}, compat=${result.compatTool}`
      )

      if (originalSourcePath) {
        const original = originalSourcePath.toLowerCase().endsWith('.zip')
          ? null
          : await this.resolveAndroidPayload(originalSourcePath)
        await this.saveSourceRecord({
          packageName: prepared.packageName,
          versionCode: prepared.versionCode,
          gameId: prepared.devkitName,
          originalSourcePath: resolve(originalSourcePath),
          originalObbPaths: [
            ...new Map(
              (original?.obbs ?? [])
                .filter((path) => basename(path).endsWith(`.${prepared.packageName}.obb`))
                .map((path) => [basename(path), path])
            ).values()
          ],
          recordedAt: Date.now()
        })
      }

      onProgress?.('Frame title installed in Steam library.', 100)
      return true
    } finally {
      if (staging) await fs.rm(staging, { recursive: true, force: true }).catch(() => {})
    }
  }
}

export default new FrameDevkitService()
