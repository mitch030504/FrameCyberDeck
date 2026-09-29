import { app } from 'electron'
import { existsSync } from 'fs'
import { promises as fs } from 'fs'
import { join } from 'path'
import { execa } from 'execa'
import settingsService from '../settingsService'

type ProgressReporter = (step: string, percent?: number) => void

export interface FrameConversionResult {
  ok: boolean
  converted: boolean
  alreadyConverted: boolean
  package?: string
  versionCode?: number
  versionName?: string
  applicationLabel?: string
  gameApk: string
  directory: string
  obbCount: number
  sha256: string
  ovrportAppVersion?: string
  ovrportRuntimeVersion?: string
  quest2FrameCommit?: string
  refreshRate?: number
  extendedCompat?: boolean
  error?: string
}

class FrameConversionService {
  private getHelperPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'frame-convert.py')
      : join(app.getAppPath(), 'resources', 'frame-convert.py')
  }

  private async resolvePython(): Promise<string> {
    const candidates = [
      process.env.FRAME_CYBERDECK_PYTHON?.trim(),
      'python3',
      'python'
    ].filter((value): value is string => Boolean(value))

    const errors: string[] = []
    for (const python of [...new Set(candidates)]) {
      try {
        await execa(python, ['--version'])
        return python
      } catch (error) {
        errors.push(`${python}: ${error instanceof Error ? error.message : String(error)}`)
      }
    }
    throw new Error(`Python 3 is required for Frame conversion. ${errors.join(' | ')}`)
  }

  private parseLastJson<T>(stdout: string): T {
    const line = stdout
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .at(-1)
    if (!line) throw new Error('Frame converter returned no result')
    try {
      return JSON.parse(line) as T
    } catch {
      throw new Error(`Frame converter returned invalid JSON: ${line}`)
    }
  }

  async doctor(): Promise<Record<string, unknown>> {
    const python = await this.resolvePython()
    const helper = this.getHelperPath()
    if (!existsSync(helper)) throw new Error(`Frame conversion helper is missing: ${helper}`)
    const result = await execa(python, [helper, 'doctor'])
    return this.parseLastJson<Record<string, unknown>>(result.stdout)
  }

  async convert(
    sourcePath: string,
    title: string,
    onProgress?: ProgressReporter
  ): Promise<{ directory: string; cleanup: () => Promise<void>; result: FrameConversionResult }> {
    const python = await this.resolvePython()
    const helper = this.getHelperPath()
    if (!existsSync(helper)) throw new Error(`Frame conversion helper is missing: ${helper}`)

    const base = join(app.getPath('temp'), 'frame-cyberdeck-converted')
    await fs.mkdir(base, { recursive: true })
    const safeTitle =
      title
        .normalize('NFKD')
        .replace(/[^A-Za-z0-9._-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60) || 'game'
    const output = await fs.mkdtemp(join(base, `${safeTitle}-`))

    const cleanup = async (): Promise<void> => {
      await fs.rm(output, { recursive: true, force: true }).catch(() => {})
    }

    try {
      onProgress?.('Converting Quest APK for Steam Frame…', 10)

      let stdout = ''
      let stderr = ''

      const stored = settingsService.getFrameConversionSettings()
      const envRefresh = process.env.FRAME_CYBERDECK_REFRESH_RATE
      const requestedRefresh = envRefresh === undefined ? stored.refreshRate : Number(envRefresh)
      const refreshRate = [0, 72, 80, 90, 120, 144].includes(requestedRefresh)
        ? requestedRefresh
        : 0
      if (requestedRefresh !== refreshRate) {
        console.warn(
          `[Frame Convert] Ignoring unsupported FRAME_CYBERDECK_REFRESH_RATE=${envRefresh}; using runtime default.`
        )
      }
      const envExtended = process.env.FRAME_CYBERDECK_EXTENDED_COMPAT
      const extendedCompat =
        envExtended === undefined
          ? stored.extendedCompat
          : /^(1|true|yes|on)$/i.test(envExtended)

      const convertArgs = [
        helper,
        'convert',
        '--input',
        sourcePath,
        '--output',
        output,
        '--refresh-rate',
        String(refreshRate)
      ]
      if (extendedCompat) convertArgs.push('--extended-compat')

      console.log(
        `[Frame Convert] Options: refresh=${refreshRate || 'default'}, extendedCompat=${extendedCompat}`
      )

      try {
        const execution = await execa(
          python,
          convertArgs,
          {
            env: process.env
          }
        )
        stdout = execution.stdout
        stderr = execution.stderr
      } catch (error) {
        const candidate = error as {
          stdout?: string
          stderr?: string
          shortMessage?: string
          message?: string
        }
        stdout = candidate.stdout ?? ''
        stderr = candidate.stderr ?? ''

        if (stderr.trim()) console.error(`[Frame Convert] ${stderr.trim()}`)
        if (stdout.trim()) {
          const parsed = this.parseLastJson<FrameConversionResult>(stdout)
          if (!parsed.ok) throw new Error(parsed.error || 'Frame conversion failed')
        }
        throw new Error(
          stderr.trim() ||
            candidate.shortMessage ||
            candidate.message ||
            'Frame conversion command failed'
        )
      }

      if (stderr.trim()) console.log(`[Frame Convert] ${stderr.trim()}`)
      const result = this.parseLastJson<FrameConversionResult>(stdout)
      if (!result.ok) throw new Error(result.error || 'Frame conversion failed')

      onProgress?.(
        result.alreadyConverted ? 'APK is already Frame-compatible.' : 'Frame conversion complete.',
        35
      )
      return { directory: result.directory, cleanup, result }
    } catch (error) {
      await cleanup()
      throw error
    }
  }
}

export default new FrameConversionService()
