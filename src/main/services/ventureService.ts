import { spawn } from 'child_process'
import { execa } from 'execa'
import type { PackageInfo, VentureGame, VentureStatus } from '@shared/types'
import { VENTURE_DEVICE_ID } from '@shared/types'

export class VentureService {
  private resolved: { path: string | null; checked: number } | null = null

  private candidates(): string[] {
    const list = [
      process.env.VENTURE_CLI_PATH?.trim(),
      'venture-cli',
      'venture-cli.exe',
      '/usr/bin/venture-cli',
      '/usr/local/bin/venture-cli',
      '/opt/Venture/venture-cli'
    ]
    const programFiles = process.env.ProgramFiles || process.env.PROGRAMFILES
    const localAppData = process.env.LOCALAPPDATA
    if (programFiles) list.push(`${programFiles}\\Venture\\venture-cli.exe`)
    if (localAppData) list.push(`${localAppData}\\Programs\\Venture\\venture-cli.exe`)
    return list.filter((value): value is string => Boolean(value))
  }

  /** Find the CLI at most once a minute; the app may start before Venture is installed. */
  async resolveCli(): Promise<string | null> {
    if (this.resolved && Date.now() - this.resolved.checked < 60_000) return this.resolved.path
    for (const candidate of this.candidates()) {
      try {
        const result = await execa(candidate, ['--json', 'status'], { timeout: 20_000 })
        JSON.parse(result.stdout)
        this.resolved = { path: candidate, checked: Date.now() }
        return candidate
      } catch {
        // not this candidate
      }
    }
    this.resolved = { path: null, checked: Date.now() }
    return null
  }

  private async runJson(
    cli: string,
    args: string[],
    timeout = 120_000
  ): Promise<Record<string, unknown> | Record<string, unknown>[]> {
    const result = await execa(cli, ['--json', ...args], { timeout })
    const text = result.stdout.trim()
    if (!text) throw new Error('Venture returned no output')
    try {
      return JSON.parse(text) as Record<string, unknown> | Record<string, unknown>[]
    } catch {
      throw new Error(`Venture returned invalid JSON: ${text.slice(0, 300)}`)
    }
  }

  async status(): Promise<VentureStatus> {
    const cli = await this.resolveCli()
    if (!cli) {
      return {
        available: false,
        cliPath: null,
        version: null,
        gamesFolder: null,
        launcherOpen: false,
        error: 'Venture CLI not found'
      }
    }
    try {
      const data = (await this.runJson(cli, ['status'], 30_000)) as Record<string, unknown>
      return {
        available: true,
        cliPath: cli,
        version: typeof data.version === 'string' ? data.version : null,
        gamesFolder: typeof data.gamesFolder === 'string' ? data.gamesFolder : null,
        launcherOpen: Boolean(data.launcherOpen),
        error: null
      }
    } catch (error) {
      return {
        available: false,
        cliPath: cli,
        version: null,
        gamesFolder: null,
        launcherOpen: false,
        error: error instanceof Error ? error.message : String(error)
      }
    }
  }

  async listGames(): Promise<VentureGame[]> {
    const cli = await this.resolveCli()
    if (!cli) throw new Error('Venture CLI not found')
    const data = await this.runJson(cli, ['library', '--installed'])
    if (!Array.isArray(data)) throw new Error('Venture returned an unexpected library response')
    return data
      .map((entry) => {
        const install = (entry.install ?? {}) as Record<string, unknown>
        return {
          id: String(entry.id ?? ''),
          name: String(entry.name ?? entry.id ?? ''),
          packageName: entry.package ? String(entry.package) : null,
          status: String(entry.status ?? ''),
          version: install.version ? String(install.version) : null,
          versionCode: typeof install.versionCode === 'number' ? install.versionCode : null,
          directory: install.directory ? String(install.directory) : null,
          complete: Boolean(install.complete)
        }
      })
      .filter((game) => game.id && game.complete)
  }

  async listInstalled(): Promise<PackageInfo[]> {
    return (await this.listGames()).map((game) => ({
      packageName: game.packageName ?? game.id,
      versionCode: game.versionCode ?? 0,
      applicationLabel: game.name
    }))
  }

  async importFolder(folder: string): Promise<string[]> {
    const cli = await this.resolveCli()
    if (!cli) throw new Error('Venture CLI not found')
    const status = await this.status()
    if (status.launcherOpen) {
      throw new Error('Close the Venture launcher before importing a game from Frame CyberDeck.')
    }
    const result = (await this.runJson(cli, ['import', folder, '--yes'], 1_800_000)) as Record<
      string,
      unknown
    >
    const imported = Array.isArray(result.imported) ? result.imported.map(String) : []
    if (imported.length === 0) throw new Error('Venture imported no games from this release')
    return imported
  }

  async play(gameId: string): Promise<boolean> {
    const cli = await this.resolveCli()
    if (!cli) throw new Error('Venture CLI not found')
    const child = spawn(cli, ['play', gameId], { detached: true, stdio: 'ignore' })
    child.unref()
    return true
  }

  async uninstall(gameId: string): Promise<boolean> {
    const cli = await this.resolveCli()
    if (!cli) throw new Error('Venture CLI not found')
    await this.runJson(cli, ['uninstall', gameId, '--yes'], 300_000)
    return true
  }

  isVentureTarget(deviceId: string): boolean {
    return deviceId === VENTURE_DEVICE_ID
  }
}

export default new VentureService()
