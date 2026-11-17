import { describe, expect, it } from 'vitest'
import { getTargetCapabilities, getTargetGameId } from './targetCapabilities'

describe('getTargetCapabilities', () => {
  it('treats Venture as a PC library without ADB or OBB actions', () => {
    const caps = getTargetCapabilities({ isVenture: true })
    expect(caps).toMatchObject({
      isVenture: true,
      supportsAdbShell: false,
      supportsObbCopy: false,
      supportsLaunch: true,
      installVerb: 'Import',
      refreshLabel: 'Refresh PC Library'
    })
  })

  it('prefers the Venture library ID over the Android package name', () => {
    expect(
      getTargetGameId({ packageName: 'com.example.game', ventureId: 'com.example.game:9:2' })
    ).toBe('com.example.game:9:2')
    expect(getTargetGameId({ packageName: 'com.example.game' })).toBe('com.example.game')
  })

  it('keeps headset capabilities for Quest and Steam Frame', () => {
    expect(getTargetCapabilities({ isVenture: false }).supportsAdbShell).toBe(true)
    expect(getTargetCapabilities({ isSteamFrame: true })).toMatchObject({
      supportsAdbShell: true,
      supportsObbCopy: true,
      supportsLaunch: true,
      installVerb: 'Install'
    })
  })
})
