import { app } from 'electron'
import { existsSync } from 'fs'
import { promises as fs } from 'fs'
import { basename, dirname, join, resolve } from 'path'
import { homedir } from 'os'
import { execa } from 'execa'

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

type ProgressReporter = (step: string, percent?: number) => void

interface DevkitRuntime {
  python: string
  pyz: string
}

class FrameDevkitService {
  private runtime: DevkitRuntime | null = null

  private getHelperPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'frame-devkit-bridge.py')
      : join(app.getAppPath(), 'resources', 'frame-devkit-bridge.py')
  }

  private devkitRoots(): string[] {
    const roots = [
      process.env.STEAMOS_DEVKIT_CLIENT_ROOT,
      join(homedir(), '.local/share/Steam/steamapps/common/SteamOSDevkitClient'),
      join(homedir(), '.steam/steam/steamapps/common/SteamOSDevkitClient'),
      join(homedir(), '.steam/root/steamapps/common/SteamOSDevkitClient'),
      join(
        homedir(),
        '.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/SteamOSDevkitClient'
      )
    ].filter((value): value is string => Boolean(value && value.trim()))

    return [...new Set(roots.map((root) => resolve(root)))]
  }

  private async findPyzFiles(root: string, depth = 3): Promise<string[]> {
    if (depth < 0 || !existsSync(root)) return []
    const result: string[] = []
    let entries
    try {
      entries = await fs.readdir(root, { withFileTypes: true })
    } catch {
      return []
    }

    for (const entry of entries) {
      const full = join(root, entry.name)
      if (entry.isFile() && /^devkit-gui(?:-cp\d+)?\.pyz$/i.test(entry.name)) {
        result.push(full)
      } else if (entry.isDirectory() && depth > 0) {
        result.push(...(await this.findPyzFiles(full, depth - 1)))
      }
    }
    return result
  }

  private pythonCandidatesForPyz(pyz: string): string[] {
    const candidates: string[] = []
    const match = basename(pyz).match(/-cp(\d)(\d+)\.pyz$/i)
    if (match) candidates.push(`python${match[1]}.${match[2]}`)
    candidates.push('python3', 'python')
    return [...new Set(candidates)]
  }

  private async resolveRuntime(): Promise<DevkitRuntime> {
    if (this.runtime) return this.runtime

    const explicitPyz = process.env.STEAMOS_DEVKIT_PYZ?.trim()
    const pyzFiles = explicitPyz ? [resolve(explicitPyz)] : []
    if (!explicitPyz) {
      for (const root of this.devkitRoots()) {
        pyzFiles.push(...(await this.findPyzFiles(root)))
      }
    }

    if (pyzFiles.length === 0) {
      throw new Error(
        'SteamOS Devkit Client was not found. Install it from Steam, or set STEAMOS_DEVKIT_PYZ to its devkit-gui-*.pyz file.'
      )
    }

    const helper = this.getHelperPath()
    if (!existsSync(helper)) {
      throw new Error(`Frame Devkit helper is missing: ${helper}`)
    }

    const failures: string[] = []
    for (const pyz of [...new Set(pyzFiles)]) {
      for (const python of this.pythonCandidatesForPyz(pyz)) {
        try {
          await execa(python, ['--version'])
          this.runtime = { python, pyz }
          return this.runtime
        } catch (error) {
          failures.push(
            `${python} for ${basename(pyz)}: ${error instanceof Error ? error.message : String(error)}`
          )
        }
      }
    }

    throw new Error(
      `SteamOS Devkit Client was found, but no compatible Python interpreter was available. ${failures.join(' | ')}`
    )
  }

  private async bridge<T extends { ok: boolean; error?: string }>(
    command: string,
    args: string[] = [],
    host = process.env.FRAME_CYBERDECK_HOST?.trim() || 'frame'
  ): Promise<T> {
    const runtime = await this.resolveRuntime()
    const helper = this.getHelperPath()
    const { stdout, stderr } = await execa(
      runtime.python,
      [helper, '--host', host, command, ...args],
      {
        env: {
          ...process.env,
          STEAMOS_DEVKIT_PYZ: runtime.pyz
        }
      }
    )

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

  async register(host?: string): Promise<void> {
    await this.bridge<{ ok: boolean; error?: string }>('register', [], host)
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

  private async resolveAndroidPayload(sourcePath: string): Promise<{ apk: string; obbs: string[] }> {
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
    return { apk, obbs }
  }

  private safeDevkitName(title: string): string {
    const safe = title
      .normalize('NFKD')
      .replace(/[^A-Za-z0-9._-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80)
    return safe || `frame-game-${Date.now()}`
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
