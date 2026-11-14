import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({
  app: {
    isPackaged: false,
    getAppPath: () => '/tmp',
    getPath: () => '/tmp',
    getVersion: () => '0.2.0'
  }
}))

import { refreshRateAssignment } from './framePortEngineService'

describe('refreshRateAssignment', () => {
  it('clears a previous custom rate with the runtime default', () => {
    expect(refreshRateAssignment(0)).toBe('refresh_rate=0')
  })

  it('writes a requested rate', () => {
    expect(refreshRateAssignment(90)).toBe('refresh_rate=90')
  })
})
