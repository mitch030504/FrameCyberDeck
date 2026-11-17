// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { fireEvent } from '@testing-library/dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TargetCard } from './DeviceList'
import type { ExtendedDeviceInfo, VentureStatus } from '@shared/types'

const ventureStatus: VentureStatus = {
  available: true,
  cliPath: '/tmp/opencode/venture-cli',
  version: '1.2.3-demo',
  gamesFolder: '/tmp/opencode/venture-games',
  launcherOpen: false,
  error: null
}

const ventureDevice: ExtendedDeviceInfo = {
  id: 'venture:pc',
  type: 'device',
  model: 'Venture',
  isQuestDevice: false,
  isVenture: true,
  ventureStatus,
  batteryLevel: null,
  storageTotal: null,
  storageFree: null,
  friendlyModelName: 'PC · Venture 1.2.3-demo',
  ipAddress: null
}

const props = {
  isConnecting: false,
  connectionError: false,
  onConnect: vi.fn(),
  onDisconnect: vi.fn(),
  onBookmark: vi.fn(),
  onDeleteBookmark: vi.fn(),
  onOpenShell: vi.fn(),
  onOpenLibrary: vi.fn(),
  isAlreadyBookmarked: false
}

afterEach(cleanup)

describe('Venture target card', () => {
  it('presents a PC library and replaces the ADB shell action', () => {
    render(<TargetCard device={ventureDevice} isConnected {...props} />)

    expect(screen.getByText('PC · VENTURE')).toBeTruthy()
    expect(screen.getByText(/PC Library/)).toBeTruthy()
    expect(screen.queryByText(/SHELL/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /OPEN LIBRARY/ }))
    expect(props.onOpenLibrary).toHaveBeenCalledTimes(1)
    expect(props.onOpenShell).not.toHaveBeenCalled()
  })

  it('surfaces the launcher-open state that blocks imports', () => {
    render(
      <TargetCard
        device={{
          ...ventureDevice,
          ventureStatus: { ...ventureStatus, launcherOpen: true }
        }}
        isConnected
        {...props}
      />
    )

    expect(screen.getByText(/LAUNCHER OPEN/)).toBeTruthy()
  })
})
