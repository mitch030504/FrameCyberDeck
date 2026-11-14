import { describe, expect, it } from 'vitest'
import type { ExtendedDeviceInfo } from './types'
import { isTcpDevice, VENTURE_DEVICE_ID } from './types'

function device(overrides: Partial<ExtendedDeviceInfo>): ExtendedDeviceInfo {
  return {
    id: 'device',
    type: 'device',
    model: null,
    isQuestDevice: false,
    batteryLevel: null,
    storageTotal: null,
    storageFree: null,
    friendlyModelName: null,
    ipAddress: null,
    ...overrides
  } as ExtendedDeviceInfo
}

describe('isTcpDevice', () => {
  it('classifies a network serial as TCP', () => {
    expect(isTcpDevice(device({ id: '192.168.1.5:5555' }))).toBe(true)
  })

  it('never classifies the Venture target as TCP', () => {
    expect(isTcpDevice(device({ id: VENTURE_DEVICE_ID, isVenture: true }))).toBe(false)
  })
})
