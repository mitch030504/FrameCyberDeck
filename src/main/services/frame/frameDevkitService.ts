import { app } from 'electron'
import { promises as fs } from 'fs'
import { basename, dirname, join, resolve } from 'path'
import { execa } from 'execa'
import type { PackageInfo } from '@shared/types'

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
  managed?: boolean
}

type ProgressReporter = (step: string, percent?: number) => void

class FrameDevkitService {
  private getHelperPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'frame-devkit-bridge.py')
      : join(app.getAppPath(), 'resources', 'frame-devkit-bridge.py')
  }

  private async resolvePython(): Promise<string> {
    const candidates = [
      process.env.FRAME_CYBERDECK_PYTHON?.trim(),
      'python3',
      'python'
    ].filter((value): value is string => Boolean(value))

    const failures: string[] = []
    for (const python of [...new Set(candidates)]) {
      try {
        await execa(python, ['--version'])
        return python
      } catch (error) {
        failures.push(
          `${python}: ${error instanceof Error ? error.message : String(error)}`
        )
      }
    }

    throw new Error(
      `Python 3 is required for Steam Frame Devkit deployment. ${failures.join(' | ')}`
    )
  }

  private async bridge<T extends { ok: boolean; error?: string }>(
    command: string,
    args: string[] = [],
    host = process.env.FRAME_CYBERDECK_HOST?.trim() || 'frame'
  ): Promise<T> {
    const python = await this.resolvePython()
    const helper = this.getHelperPath()

    let stdout = ''
    let stderr = ''
    try {
      const result = await execa(python, [helper, '--host', host, command, ...args])
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

      const failedLine = stdout
        .trim()
        .split(/\r?\n/)
        .filter(Boolean)
        .at(-1)

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
    const line = stdout
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .at(-1)
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

  async register(_host?: string): Promise<void> {
    throw new Error(
      'Register the Steam Frame once in the official SteamOS Devkit Client; Frame CyberDeck reuses that Devkit SSH identity.'
    )
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
        throw new Error(`Frame Devkit deploy currently accepts an APK or extracted game folder: ${source}`)
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
    const obbs = allFiles.filter((file) => file.toLowerCase().endsWith('.obb'))
    return { apk, obbs, root: searchRoot }
  }

  private async readConversionMetadata(root: string): Promise<{
    packageName?: string
    versionCode?: number
    versionName?: string
  }> {
    const path = join(root, 'frame-conversion.json')
    try {
      const parsed = JSON.parse(await fs.readFile(path, 'utf-8')) as {
        package?: unknown
        packageName?: unknown
        versionCode?: unknown
        versionName?: unknown
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
      return { packageName, versionCode, versionName }
    } catch {
      return {}
    }
  }

  private safeDevkitName(title: string): string {
    const safe = title
      .normalize('NFKD')
      .replace(/[^A-Za-z0-9._]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 80)
    return safe || `frame_game_${Date.now()}`
  }

  private async createStagingDirectory(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter
  ): Promise<{ directory: string; devkitName: string }> {
    onProgress?.('Analyzing Android package…', 5)
    const payload = await this.resolveAndroidPayload(sourcePath)
    const devkitName = this.safeDevkitName(title)
    const base = join(app.getPath('temp'), 'frame-cyberdeck')
    await fs.mkdir(base, { recursive: true })
    const directory = await fs.mkdtemp(join(base, `${devkitName}-`))

    onProgress?.('Preparing Frame title…', 15)
    await fs.copyFile(payload.apk, join(directory, 'game.apk'))

    const conversion = await this.readConversionMetadata(payload.root)
    await fs.writeFile(
      join(directory, 'frame-cyberdeck.json'),
      JSON.stringify(
        {
          schemaVersion: 1,
          gameId: devkitName,
          title,
          packageName: conversion.packageName ?? devkitName,
          versionCode: conversion.versionCode ?? 0,
          versionName: conversion.versionName ?? ''
        },
        null,
        2
      ),
      'utf-8'
    )

    if (payload.obbs.length > 0) {
      const obbDir = join(directory, 'obb')
      await fs.mkdir(obbDir, { recursive: true })
      const names = new Set<string>()
      let copied = 0
      for (const obb of payload.obbs) {
        const name = basename(obb)
        if (names.has(name)) {
          throw new Error(`Two OBB files have the same filename (${name}); refusing an ambiguous upload.`)
        }
        names.add(name)
        await fs.copyFile(obb, join(obbDir, name))
        copied++
        onProgress?.(
          `Preparing OBB data (${copied}/${payload.obbs.length})…`,
          15 + Math.round((copied / payload.obbs.length) * 20)
        )
      }
    }

    return { directory, devkitName }
  }

  async listGames(host?: string): Promise<FrameInstalledGame[]> {
    const result = await this.bridge<{
      ok: boolean
      error?: string
      games: FrameInstalledGame[]
    }>('list', [], host)
    return result.games
  }

  private selectGame(games: FrameInstalledGame[], packageName: string): FrameInstalledGame | undefined {
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
      versionCode: Number.isFinite(game.versionCode) ? game.versionCode : 0
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
    await this.bridge<{ ok: boolean; error?: string }>('run', ['--name', gameId], host)
    return true
  }

  async runPackage(packageName: string, host?: string): Promise<boolean> {
    const games = await this.listGames(host)
    const game = this.selectGame(games, packageName)
    if (!game) {
      console.warn(`[Frame Devkit] No installed title found for ${packageName}`)
      return false
    }
    return await this.runGame(game.gameid, host)
  }

  async deploy(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter,
    host?: string
  ): Promise<boolean> {
    let staging: string | null = null
    try {
      const status = await this.status(host)
      if (!status.paired) {
        throw new Error(
          `Steam Frame ${status.address || status.host} is visible but not paired with SteamOS Devkit Client. On Frame choose Settings > Developer > Pair new host, then register it in SteamOS Devkit Client.`
        )
      }

      const prepared = await this.createStagingDirectory(sourcePath, title, onProgress)
      staging = prepared.directory
      onProgress?.('Uploading to Steam Frame with SteamOS Devkit…', 40)

      const result = await this.bridge<FrameDeployResult>(
        'deploy',
        [
          '--name',
          prepared.devkitName,
          '--directory',
          prepared.directory,
          '--start-command',
          'game.apk'
        ],
        host
      )

      console.log(
        `[Frame Devkit] Deployed ${result.name}: ${result.startCommand}, runtime=${result.runtime}, compat=${result.compatTool}`
      )
      onProgress?.('Frame title installed in Steam library.', 100)
      return true
    } finally {
      if (staging) await fs.rm(staging, { recursive: true, force: true }).catch(() => {})
    }
  }
}

export default new FrameDevkitService()
