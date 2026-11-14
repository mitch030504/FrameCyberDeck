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
        applicationLabel: 'Ready Game'
      }
    ])
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
})
