import { beforeEach, describe, expect, it, vi } from 'vitest'

const execaMock = vi.hoisted(() => vi.fn())
vi.mock('execa', () => ({ execa: execaMock }))

import { VentureService } from './ventureService'

function replies(map: (args: string[]) => unknown): void {
  execaMock.mockImplementation(async (_command: string, args: string[]) => ({
    stdout: JSON.stringify(map(args))
  }))
}

beforeEach(() => {
  execaMock.mockReset()
})

describe('VentureService', () => {
  it('maps installed library entries and skips incomplete installs', async () => {
    replies((args) => {
      if (args.includes('status')) return { version: '1.2.3', gamesFolder: '/games' }
      return [
        {
          id: 'com.ready.game',
          name: 'Ready Game',
          package: 'com.ready.game',
          status: 'Installed',
          install: { version: '1.0', versionCode: 7, directory: '/games/ready', complete: true }
        },
        {
          id: 'com.partial.game',
          name: 'Partial Game',
          package: 'com.partial.game',
          status: 'Downloading',
          install: { version: '2.0', versionCode: 8, directory: '/games/partial', complete: false }
        }
      ]
    })

    const service = new VentureService()
    const games = await service.listGames()
    expect(games).toHaveLength(1)
    expect(games[0]).toMatchObject({
      id: 'com.ready.game',
      packageName: 'com.ready.game',
      version: '1.0',
      versionCode: 7,
      complete: true
    })

    const installed = await service.listInstalled()
    expect(installed).toEqual([
      {
        packageName: 'com.ready.game',
        versionCode: 7,
        applicationLabel: 'Ready Game',
        ventureId: 'com.ready.game'
      }
    ])
  })

  it('parses 0.2.5 numeric-string version codes and rejects non-numeric ones', async () => {
    replies((args) => {
      if (args.includes('status')) return { version: '0.2.5' }
      return [
        {
          id: 'com.ready.game',
          name: 'Ready Game',
          install: { versionCode: '50', complete: true }
        },
        {
          id: 'com.bad.version',
          name: 'Bad Version',
          install: { versionCode: 'beta', complete: true }
        }
      ]
    })

    const service = new VentureService()
    const games = await service.listGames()
    expect(games.find((game) => game.id === 'com.ready.game')?.versionCode).toBe(50)
    expect(games.find((game) => game.id === 'com.bad.version')?.versionCode).toBeNull()
  })

  it('imports a release folder and returns the imported ids', async () => {
    replies((args) => {
      if (args.includes('status')) return { version: '1.2.3', launcherOpen: false }
      if (args.includes('import')) return { imported: ['com.ready.game'] }
      throw new Error(`unexpected arguments: ${args.join(' ')}`)
    })

    const service = new VentureService()
    await expect(service.importFolder('/downloads/ready')).resolves.toEqual(['com.ready.game'])

    expect(execaMock).toHaveBeenCalledWith(
      'venture-cli',
      ['--json', 'import', '/downloads/ready', '--yes'],
      expect.objectContaining({ timeout: 1_800_000 })
    )
  })

  it('refuses to import while the launcher is open', async () => {
    replies((args) => {
      if (args.includes('status')) return { version: '1.2.3', launcherOpen: true }
      return {}
    })

    const service = new VentureService()
    await expect(service.importFolder('/downloads/ready')).rejects.toThrow(
      /Close the Venture launcher/
    )
  })

  it('reports an unavailable CLI when no candidate answers', async () => {
    execaMock.mockRejectedValue(new Error('not found'))
    const service = new VentureService()
    const status = await service.status()
    expect(status.available).toBe(false)
    expect(status.error).toBe('Venture CLI not found')
  })

  it('uses the saved CLI path instead of auto-discovery', async () => {
    replies(() => ({ version: '9.9.9', launcherOpen: false }))

    const service = new VentureService()
    service.setCliOverride('/custom/venture-cli')
    const status = await service.status()

    expect(status.available).toBe(true)
    expect(status.cliPath).toBe('/custom/venture-cli')
    expect(execaMock).toHaveBeenCalledWith(
      '/custom/venture-cli',
      ['--json', 'status'],
      expect.objectContaining({ timeout: 20_000 })
    )
    expect(execaMock).not.toHaveBeenCalledWith('venture-cli', expect.anything(), expect.anything())
  })

  it('clears the cached miss when forced to re-check', async () => {
    let cliAvailable = false
    execaMock.mockImplementation(async (_command: string, args: string[]) => {
      if (!cliAvailable) throw new Error('not found')
      if (args.includes('status')) return { stdout: JSON.stringify({ version: '1.0.0' }) }
      return { stdout: JSON.stringify({}) }
    })

    const service = new VentureService()
    await expect(service.status()).resolves.toMatchObject({ available: false })

    cliAvailable = true
    await expect(service.status()).resolves.toMatchObject({ available: false })
    await expect(service.status(true)).resolves.toMatchObject({ available: true })
  })

  it('passes opaque colon-containing ids as a single argument', async () => {
    replies((args) => {
      if (args.includes('status')) return { version: '1.2.3' }
      if (args.includes('uninstall')) return {}
      throw new Error(`unexpected arguments: ${args.join(' ')}`)
    })

    const service = new VentureService()
    await expect(service.uninstall('com.ready.game:1:2')).resolves.toBe(true)

    expect(execaMock).toHaveBeenCalledWith(
      'venture-cli',
      ['--json', 'uninstall', 'com.ready.game:1:2', '--yes'],
      expect.objectContaining({ timeout: 300_000 })
    )
  })
})
