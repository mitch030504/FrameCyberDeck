import { app } from 'electron'
import { createHash } from 'crypto'
import { existsSync, promises as fs } from 'fs'
import { join } from 'path'
import { execa } from 'execa'
import type { FrameConversionResult } from './frameConversionService'
import type { FramePortEngineStatus, FramePortEngineTool } from '@shared/types'

export interface FramePortEngineEvent {
  kind: 'log' | 'stage' | 'progress' | 'check' | 'done' | 'error'
  stage?: string
  message?: string
  fraction?: number
  data?: Record<string, unknown>
}

export type { FramePortEngineStatus, FramePortEngineTool }

export interface FramePortEngineGame {
  package: string
  title: string
  name: string
  apk: string
  dataDir: string | null
  status: string | null
  warnings: string[]
}

type ProgressReporter = (step: string, percent?: number) => void

/** Always written, including 0, so a previous recipe override is cleared. */
export function refreshRateAssignment(rate: number): string {
  return `refresh_rate=${rate}`
}

const ENGINE_FILES = [
  'vendor/frameport/src/frameport/pipeline.py',
  'vendor/frameport/requirements.txt'
]

class FramePortEngineService {
  private preparePromise: Promise<FramePortEngineStatus> | null = null

  private appRoot(): string {
    return app.isPackaged ? process.resourcesPath : app.getAppPath()
  }

  private vendorRoot(): string {
    return join(this.appRoot(), 'vendor', 'frameport')
  }

  private runnerPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'frameport-engine.py')
      : join(app.getAppPath(), 'resources', 'frameport-engine.py')
  }

  private requirementsPath(): string {
    return join(this.vendorRoot(), 'requirements.txt')
  }

  private engineRoot(): string {
    return join(app.getPath('userData'), 'frameport-engine')
  }

  private venvRoot(): string {
    return join(this.engineRoot(), 'venv')
  }

  private homeRoot(): string {
    return join(this.engineRoot(), 'home')
  }

  private buildsRoot(): string {
    return join(this.engineRoot(), 'builds')
  }

  private venvPython(): string {
    return process.platform === 'win32'
      ? join(this.venvRoot(), 'Scripts', 'python.exe')
      : join(this.venvRoot(), 'bin', 'python')
  }

  private filesReady(): boolean {
    return ENGINE_FILES.every((relative) => existsSync(join(this.appRoot(), relative)))
  }

  private async requirementsStamp(): Promise<string> {
    const data = await fs.readFile(this.requirementsPath())
    return createHash('sha256')
      .update(data)
      .update(`${process.platform}-${process.arch}-${process.version}`)
      .digest('hex')
  }

  private async venvReady(): Promise<boolean> {
    if (!existsSync(this.venvPython())) return false
    try {
      const stamp = await this.requirementsStamp()
      const stored = await fs.readFile(join(this.venvRoot(), '.frameport-requirements'), 'utf-8')
      return stored.trim() === stamp
    } catch {
      return false
    }
  }

  private runEnv(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      FRAMEPORT_HOME: this.homeRoot(),
      PYTHONUNBUFFERED: '1',
      PYTHONDONTWRITEBYTECODE: '1'
    }
  }

  private async runJsonLines(
    command: string,
    args: string[],
    onEvent?: (event: FramePortEngineEvent) => void
  ): Promise<Record<string, unknown>> {
    const python = this.venvPython()
    const child = execa(python, [this.runnerPath(), command, ...args], {
      env: this.runEnv(),
      buffer: false,
      reject: false,
      timeout: 0
    })

    let final: Record<string, unknown> | null = null
    let failure: string | null = null
    let buffer = ''

    const consume = (chunk: Buffer): void => {
      buffer += chunk.toString('utf8')
      let index = buffer.indexOf('\n')
      while (index >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)
        if (line)
          this.handleLine(
            line,
            onEvent,
            (result) => (final = result),
            (message) => (failure = message)
          )
        index = buffer.indexOf('\n')
      }
    }

    child.stdout?.on('data', consume)
    child.stderr?.on('data', (chunk: Buffer) => {
      const text = chunk.toString('utf8').trim()
      if (text) console.log(`[FramePort Engine] ${text}`)
    })

    const result = await child
    if (buffer.trim()) {
      this.handleLine(
        buffer.trim(),
        onEvent,
        (value) => (final = value),
        (message) => (failure = message)
      )
    }
    if (result.exitCode !== 0 && !failure) {
      failure = `FramePort engine exited with ${result.exitCode}`
    }
    if (failure) throw new Error(failure)
    if (!final) throw new Error('FramePort engine returned no result')
    return final
  }

  private handleLine(
    line: string,
    onEvent: ((event: FramePortEngineEvent) => void) | undefined,
    setFinal: (result: Record<string, unknown>) => void,
    setFailure: (message: string) => void
  ): void {
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(line) as Record<string, unknown>
    } catch {
      console.warn(`[FramePort Engine] Ignoring non-JSON output: ${line.slice(0, 300)}`)
      return
    }
    if (parsed.type === 'error') {
      setFailure(String(parsed.message ?? 'FramePort engine failed'))
      return
    }
    if (parsed.type === 'result') {
      setFinal(parsed)
      return
    }
    if (parsed.type === 'event' && onEvent) {
      onEvent(parsed as unknown as FramePortEngineEvent)
    }
  }

  private async resolveSystemPython(): Promise<string> {
    const candidates = [process.env.FRAME_CYBERDECK_PYTHON?.trim(), 'python3', 'python'].filter(
      (value): value is string => Boolean(value)
    )
    const failures: string[] = []
    for (const candidate of [...new Set(candidates)]) {
      try {
        const result = await execa(candidate, ['--version'])
        const match = /(\d+)\.(\d+)/.exec(result.stdout || result.stderr)
        if (!match) throw new Error('no version reported')
        const major = Number(match[1])
        const minor = Number(match[2])
        if (major > 3 || (major === 3 && minor >= 11)) return candidate
        failures.push(`${candidate}: Python ${major}.${minor} is too old`)
      } catch (error) {
        failures.push(`${candidate}: ${error instanceof Error ? error.message : String(error)}`)
      }
    }
    throw new Error(`FramePort needs Python 3.11 or newer. ${failures.join(' | ')}`)
  }

  private async ensureVenv(onProgress?: ProgressReporter): Promise<void> {
    await fs.mkdir(this.engineRoot(), { recursive: true })
    if (await this.venvReady()) return

    if (!existsSync(this.venvPython())) {
      const python = await this.resolveSystemPython()
      onProgress?.('Creating the FramePort Python environment…', 5)
      await fs.rm(this.venvRoot(), { recursive: true, force: true })
      await execa(python, ['-m', 'venv', this.venvRoot()], { timeout: 300_000 })
    }

    onProgress?.('Installing FramePort engine dependencies…', 10)
    await execa(
      this.venvPython(),
      ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', this.requirementsPath()],
      { timeout: 1_800_000 }
    )
    const stamp = await this.requirementsStamp()
    await fs.writeFile(join(this.venvRoot(), '.frameport-requirements'), `${stamp}\n`, 'utf-8')
  }

  private async readTools(): Promise<FramePortEngineTool[]> {
    if (!(await this.venvReady())) return []
    try {
      const result = await this.runJsonLines('tools-status', [])
      return (result.tools as FramePortEngineTool[]) ?? []
    } catch (error) {
      console.warn('[FramePort Engine] tools-status failed:', error)
      return []
    }
  }

  async getStatus(): Promise<FramePortEngineStatus> {
    const filesReady = this.filesReady()
    const venvReady = filesReady && (await this.venvReady())
    const tools = venvReady ? await this.readTools() : []
    const required = ['java', 'overport', 'apksigner']
    const toolsReady =
      required.every((name) => tools.some((tool) => tool.name === name && tool.installed)) &&
      tools.length > 0
    let engineVersion: string | null = null
    if (venvReady) {
      try {
        const probe = await this.runJsonLines('probe', [])
        engineVersion = String(probe.version ?? '')
      } catch {
        /* status still reports the rest */
      }
    }
    const status: FramePortEngineStatus = {
      filesReady,
      venvReady,
      toolsReady,
      engineVersion,
      python: venvReady ? this.venvPython() : null,
      tools,
      error: filesReady ? null : 'Vendored FramePort engine files are missing'
    }
    return status
  }

  async prepare(onProgress?: ProgressReporter): Promise<FramePortEngineStatus> {
    if (this.preparePromise) return this.preparePromise
    this.preparePromise = (async () => {
      if (!this.filesReady()) {
        throw new Error(
          'Vendored FramePort engine files are missing. Run scripts/sync-frameport-engine.py.'
        )
      }
      await fs.mkdir(this.homeRoot(), { recursive: true })
      await fs.mkdir(this.buildsRoot(), { recursive: true })
      await this.ensureVenv(onProgress)

      const tools = await this.readTools()
      const required = ['java', 'overport', 'apksigner']
      if (!required.every((name) => tools.some((tool) => tool.name === name && tool.installed))) {
        onProgress?.('Downloading FramePort tools (Java, OVRPort, apksigner)…', 20)
        await this.runJsonLines('tools-install', [], (event) => {
          if (event.message) console.log(`[FramePort Engine] ${event.message}`)
        })
      }
      const status = await this.getStatus()
      return status
    })()
      .catch((error) => {
        throw error
      })
      .finally(() => {
        this.preparePromise = null
      })
    return this.preparePromise
  }

  async scan(sourcePath: string): Promise<FramePortEngineGame[]> {
    if (!(await this.venvReady())) throw new Error('FramePort engine is not prepared')
    const result = await this.runJsonLines('scan', [sourcePath])
    return ((result.games as Record<string, unknown>[]) ?? []).map((game) => ({
      package: String(game.package ?? ''),
      title: String(game.title ?? ''),
      name: String(game.name ?? ''),
      apk: String(game.apk ?? ''),
      dataDir: game.data_dir ? String(game.data_dir) : null,
      status: game.status ? String(game.status) : null,
      warnings: Array.isArray(game.warnings) ? game.warnings.map(String) : []
    }))
  }

  async build(
    packageName: string,
    outdir: string,
    onEvent?: (event: FramePortEngineEvent) => void
  ): Promise<Record<string, unknown>> {
    return this.runJsonLines('build', [packageName, '--outdir', outdir], onEvent)
  }

  async recipeSet(packageName: string, assignments: string[]): Promise<void> {
    const args = [packageName, ...assignments.flatMap((item) => ['--set', item])]
    await this.runJsonLines('recipe-set', args)
  }

  async collect(
    packageName: string,
    sourcePath: string,
    outputDir: string,
    builtApk: string
  ): Promise<FrameConversionResult> {
    const result = await this.runJsonLines('collect', [
      packageName,
      '--source',
      sourcePath,
      '--output',
      outputDir,
      '--apk',
      builtApk
    ])
    return {
      ok: Boolean(result.ok),
      converted: Boolean(result.converted),
      alreadyConverted: Boolean(result.alreadyConverted),
      package: String(result.package ?? packageName),
      versionCode: Number(result.versionCode ?? 0),
      versionName: String(result.versionName ?? ''),
      applicationLabel: String(result.applicationLabel ?? ''),
      gameApk: String(result.gameApk ?? ''),
      directory: String(result.directory ?? outputDir),
      obbCount: Number(result.obbCount ?? 0),
      sha256: String(result.sha256 ?? ''),
      frameportCommit: String(result.frameportCommit ?? '')
    }
  }

  /**
   * Build a release with the vendored FramePort engine. The caller owns the
   * output directory; the engine's scratch build is removed afterwards.
   */
  async convert(
    sourcePath: string,
    packageName: string | undefined,
    outputDir: string,
    refreshRate: number,
    onProgress?: ProgressReporter
  ): Promise<FrameConversionResult> {
    if (!(await this.venvReady())) {
      throw new Error('FramePort engine is not prepared')
    }
    onProgress?.('Analyzing release with FramePort…', 10)
    const games = await this.scan(sourcePath)
    if (games.length === 0) throw new Error('FramePort found no Android app in this release')
    const game = packageName
      ? games.find((item) => item.package === packageName)
      : games.length === 1
        ? games[0]
        : undefined
    if (!game) {
      throw new Error(
        `FramePort found several apps (${games.map((item) => item.package).join(', ')}); cannot choose one`
      )
    }

    await fs.mkdir(this.buildsRoot(), { recursive: true })
    const buildDir = await fs.mkdtemp(join(this.buildsRoot(), 'build-'))
    try {
      onProgress?.(
        refreshRate ? `Setting refresh rate to ${refreshRate} Hz…` : 'Using the runtime refresh rate…',
        12
      )
      // Always write the setting, including 0: a previous conversion may have
      // persisted a custom rate in this engine's recipe.
      await this.recipeSet(game.package, [refreshRateAssignment(refreshRate)])
      let lastStage = ''
      const result = await this.build(game.package, buildDir, (event) => {
        if (event.kind === 'stage' && event.message && event.message !== lastStage) {
          lastStage = event.message
          onProgress?.(`FramePort: ${event.message}`, 15)
        }
        if (event.kind === 'progress' && typeof event.fraction === 'number') {
          onProgress?.('Building with FramePort…', 15 + Math.round(event.fraction * 20))
        }
        if (event.kind === 'log' && event.message) {
          console.log(`[FramePort Engine] ${event.message}`)
        }
        if (event.kind === 'check' && event.message) {
          console.log(`[FramePort Engine] check: ${event.message}`)
        }
      })
      const builtApk = String(result.apk ?? '')
      if (!builtApk) throw new Error('FramePort build returned no APK')
      onProgress?.('Collecting converted release…', 36)
      return await this.collect(game.package, sourcePath, outputDir, builtApk)
    } finally {
      await fs.rm(buildDir, { recursive: true, force: true }).catch(() => {})
    }
  }
}

export default new FramePortEngineService()
