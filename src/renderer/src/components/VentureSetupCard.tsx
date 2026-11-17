import React from 'react'
import type { VentureStatus } from '@shared/types'

interface VentureSetupCardProps {
  status: VentureStatus | null
  onSetup: () => void
  onRescan?: () => void
}

const S = { fontFamily: 'var(--vrcd-font-mono)' }

/**
 * Shown in Target Acquisition when the external Venture CLI is missing or not
 * usable. Keeps the PC target discoverable instead of silently hiding it.
 */
const VentureSetupCard: React.FC<VentureSetupCardProps> = ({ status, onSetup, onRescan }) => {
  if (status?.available) return null

  const detail =
    status?.error ||
    (status === null ? 'Checking for the Venture CLI…' : 'venture-cli was not found.')

  return (
    <div
      data-testid="venture-setup-card"
      style={{
        background: 'rgba(var(--vrcd-purple-raw),0.04)',
        border: '1px solid rgba(var(--vrcd-purple-raw),0.35)',
        borderRadius: '6px',
        padding: '12px 16px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: '12px',
        marginBottom: '8px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', minWidth: 0 }}>
        <div style={{ fontSize: '24px', flexShrink: 0, lineHeight: 1, paddingTop: '2px' }}>💻</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span
              style={{
                ...S,
                color: 'var(--vrcd-purple)',
                fontWeight: 'bold',
                fontSize: '14px',
                letterSpacing: '0.06em'
              }}
            >
              PC · VENTURE
            </span>
            <span
              style={{
                ...S,
                fontSize: '9px',
                letterSpacing: '0.14em',
                padding: '2px 7px',
                border: '1px solid rgba(var(--vrcd-purple-raw),0.6)',
                color: 'var(--vrcd-purple)',
                borderRadius: '3px',
                flexShrink: 0
              }}
            >
              {status === null ? 'CHECKING' : status.cliPath ? 'CLI ERROR' : 'CLI NOT FOUND'}
            </span>
            <span
              style={{
                ...S,
                fontSize: '9px',
                letterSpacing: '0.14em',
                color: 'rgba(var(--vrcd-purple-raw),0.6)'
              }}
            >
              EXPERIMENTAL
            </span>
          </div>
          <div
            style={{
              ...S,
              fontSize: '10px',
              color: 'rgba(var(--vrcd-purple-raw),0.75)',
              lineHeight: 1.6,
              wordBreak: 'break-word'
            }}
          >
            {detail}
            <br />
            External PC library. Install Venture separately, then point Frame CyberDeck at its CLI.
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', flexShrink: 0 }}>
        <button
          className="breach-btn breach-btn-purple"
          onClick={onSetup}
          style={{ fontSize: '10px', padding: '5px 12px' }}
        >
          ⚙ SET UP
        </button>
        {onRescan && (
          <button
            className="breach-btn"
            onClick={onRescan}
            style={{ fontSize: '10px', padding: '4px 10px' }}
          >
            ↺ RESCAN
          </button>
        )}
      </div>
    </div>
  )
}

export default VentureSetupCard
