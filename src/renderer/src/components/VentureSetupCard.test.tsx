// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { fireEvent } from '@testing-library/dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import VentureSetupCard from './VentureSetupCard'
import type { VentureStatus } from '@shared/types'

const unavailable: VentureStatus = {
  available: false,
  cliPath: null,
  version: null,
  gamesFolder: null,
  launcherOpen: false,
  error: 'Venture CLI not found'
}

afterEach(cleanup)

describe('VentureSetupCard', () => {
  it('keeps the PC target discoverable when the CLI is missing', () => {
    const onSetup = vi.fn()
    const onRescan = vi.fn()
    render(<VentureSetupCard status={unavailable} onSetup={onSetup} onRescan={onRescan} />)

    expect(screen.getByTestId('venture-setup-card').textContent).toContain('Venture CLI not found')
    expect(screen.getByText('CLI NOT FOUND')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /SET UP/ }))
    fireEvent.click(screen.getByRole('button', { name: /RESCAN/ }))
    expect(onSetup).toHaveBeenCalledTimes(1)
    expect(onRescan).toHaveBeenCalledTimes(1)
  })

  it('renders nothing once the CLI is available', () => {
    const { container } = render(
      <VentureSetupCard status={{ ...unavailable, available: true }} onSetup={() => {}} />
    )
    expect(container.firstChild).toBeNull()
  })
})
