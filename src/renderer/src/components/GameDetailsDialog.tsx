import React, { useEffect, useState } from 'react'
import { GameInfo, isSignatureMismatchError } from '@shared/types'
import {
  Dialog,
  DialogSurface,
  DialogBody,
  Button,
  Spinner,
  ProgressBar,
  Text
} from '@fluentui/react-components'
import {
  ArrowClockwiseRegular,
  DismissRegular,
  DocumentDataRegular,
  CalendarClockRegular,
  ArrowDownloadRegular as DownloadIcon,
  TagRegular,
  DeleteRegular,
  ArrowSyncRegular,
  ArrowUpRegular,
  InfoRegular,
  CheckmarkCircleRegular,
  StarFilled,
  StarRegular,
  BroomRegular as UninstallIcon,
  PlayRegular
} from '@fluentui/react-icons'
import placeholderImage from '../assets/images/game-placeholder.png'
import { useGames } from '@renderer/hooks/useGames'
import { useAdb } from '@renderer/hooks/useAdb'
import { getSideloadingDisabled } from '@renderer/hooks/useExtrasSettings'
import ErrorDetailDialog, { ErrorPhase } from './ErrorDetailDialog'
import NoteRenderer from './NoteRenderer'
import GameSaveBackupControls from './backup/GameSaveBackupControls'
import GameCoverLightbox from './GameCoverLightbox'
import GameDescriptionPanel from './GameDescriptionPanel'
import { gameDescriptionKey, toGameDescriptionRequest } from '@renderer/utils/gameDescription'
import { localFileUrl } from '../utils/localFileUrl'

const NEON = 'var(--vrcd-neon)'
const PURPLE = 'var(--vrcd-purple)'
const BG = '#030310'
const SURFACE_VARS = {
  '--colorNeutralBackground1': BG,
  '--colorNeutralBackground2': '#050520',
  '--colorNeutralBackground3': '#040418',
  '--colorNeutralForeground1': NEON,
  '--colorNeutralForeground2': 'rgba(var(--vrcd-neon-raw),0.75)',
  '--colorNeutralForeground3': 'rgba(var(--vrcd-neon-raw),0.5)',
  '--colorNeutralStroke1': 'rgba(var(--vrcd-neon-raw),0.2)',
  '--colorBrandBackground': NEON,
  '--colorNeutralForegroundOnBrand': BG,
  '--colorPaletteRedForeground1': '#ff5555',
  '--colorPaletteRedBackground2': 'rgba(255,50,50,0.12)'
} as React.CSSProperties

interface GameDetailsDialogProps {
  game: GameInfo | null
  open: boolean
  onClose: () => void
  downloadStatusMap: Map<
    string,
    { status: string; progress: number; error?: string; downloadPath?: string }
  >
  onInstall: (game: GameInfo) => void
  onUninstall: (game: GameInfo) => Promise<void>
  onReinstall: (game: GameInfo) => Promise<void>
  onUpdate: (game: GameInfo) => Promise<void>
  onRetry: (game: GameInfo) => void
  onCancelDownload: (game: GameInfo) => void
  onDeleteDownloaded: (game: GameInfo) => void
  onInstallFromCompleted: (game: GameInfo) => void
  onUninstallAndUpdate: (game: GameInfo) => Promise<void>
  onDismissUpdateError: (game: GameInfo) => void
  onLaunchFrame: (game: GameInfo) => Promise<void>
  getNote: (releaseName: string) => Promise<string | null>
  isConnected: boolean
  isBusy: boolean
  isStarred: boolean
  onToggleStarred: () => void
}

const GameDetailsDialog: React.FC<GameDetailsDialogProps> = ({
  game,
  open,
  onClose,
  downloadStatusMap,
  onInstall,
  onUninstall,
  onReinstall,
  onUpdate,
  onRetry,
  onCancelDownload,
  onDeleteDownloaded,
  onInstallFromCompleted,
  onUninstallAndUpdate,
  onDismissUpdateError,
  onLaunchFrame,
  getNote,
  isConnected,
  isBusy,
  isStarred,
  onToggleStarred
}) => {
  const { getDescription, descriptionSnapshot } = useGames()
  const { selectedDevice, selectedDeviceDetails } = useAdb()
  const [currentGameNote, setCurrentGameNote] = useState<string | null>(null)
  const [loadingNote, setLoadingNote] = useState(false)
  const [errorDetailOpen, setErrorDetailOpen] = useState(false)
  const [loadingDescription, setLoadingDescription] = useState(false)
  useEffect(() => {
    let alive = true
    if (open && game?.releaseName) {
      setLoadingNote(true)
      setCurrentGameNote(null)
      getNote(game.releaseName)
        .then((n) => {
          if (alive) setCurrentGameNote(n)
        })
        .catch(() => {
          if (alive) setCurrentGameNote('Error loading note.')
        })
        .finally(() => {
          if (alive) setLoadingNote(false)
        })
    }
    return () => {
      alive = false
    }
  }, [open, game, getNote])

  const descriptionResult = game
    ? (descriptionSnapshot[gameDescriptionKey(game, 'en')] ?? null)
    : null

  useEffect(() => {
    let alive = true
    if (!open || !game) {
      setLoadingDescription(false)
      return () => {
        alive = false
      }
    }

    if (descriptionResult) {
      setLoadingDescription(false)
      return () => {
        alive = false
      }
    }

    setLoadingDescription(true)
    getDescription(toGameDescriptionRequest(game, 'en'))
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoadingDescription(false)
      })
    return () => {
      alive = false
    }
  }, [open, game, getDescription, descriptionResult])

  const renderActionButtons = (g: GameInfo): React.ReactNode => {
    const status = downloadStatusMap.get(g.releaseName || '')?.status
    const canCancel = status === 'Downloading' || status === 'Extracting' || status === 'Queued'
    const isDownloaded = status === 'Completed'
    const isInstallError = status === 'InstallError'
    const isErrorOrCancelled = status === 'Error' || status === 'Cancelled'
    const isInstalling = status === 'Installing'
    const noSideload = getSideloadingDisabled()
    const isSignatureMismatch =
      isInstallError && isSignatureMismatchError(downloadStatusMap.get(g.releaseName || '')?.error)

    if (isInstalling)
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Spinner size="small" />
          <Text>Installing...</Text>
        </div>
      )
    if (canCancel)
      return (
        <Button
          appearance="secondary"
          style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
          icon={<DismissRegular />}
          onClick={() => onCancelDownload(g)}
          disabled={isBusy}
        >
          Cancel Download
        </Button>
      )
    if (isSignatureMismatch)
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div
            style={{
              fontSize: 12,
              fontFamily: 'monospace',
              color: '#ff5555',
              lineHeight: 1.5,
              border: '1px solid rgba(255,85,85,0.4)',
              borderRadius: 6,
              padding: '8px 10px',
              background: 'rgba(255,50,50,0.06)'
            }}
          >
            The installed version of this app was signed with a different certificate than this
            update. To install the update, the existing app must be uninstalled first -{' '}
            <strong>this will erase its save data, progress, and settings.</strong>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {!noSideload && (
              <Button
                appearance="secondary"
                style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.7)', fontWeight: 600 }}
                icon={<UninstallIcon />}
                onClick={() => onUninstallAndUpdate(g)}
                disabled={!isConnected || isBusy}
              >
                Uninstall &amp; Update (Erase Save Data)
              </Button>
            )}
            <Button
              appearance="secondary"
              onClick={() => onDismissUpdateError(g)}
              disabled={isBusy}
            >
              Don&apos;t Update
            </Button>
            <Button
              appearance="secondary"
              icon={<InfoRegular />}
              style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
              onClick={() => setErrorDetailOpen(true)}
            >
              Error info
            </Button>
          </div>
        </div>
      )
    if (isInstallError || isErrorOrCancelled)
      return (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button
            appearance="primary"
            icon={<ArrowClockwiseRegular />}
            onClick={() => onRetry(g)}
            disabled={isBusy}
          >
            Retry
          </Button>
          {(isInstallError || status === 'Error') && (
            <Button
              appearance="secondary"
              icon={<InfoRegular />}
              style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
              onClick={() => setErrorDetailOpen(true)}
            >
              Error info
            </Button>
          )}
          <Button
            appearance="secondary"
            style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
            icon={<DeleteRegular />}
            onClick={() => onDeleteDownloaded(g)}
            disabled={isBusy}
          >
            Delete Files
          </Button>
        </div>
      )
    if (g.isInstalled) {
      if (g.hasUpdate)
        return (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {selectedDeviceDetails?.isSteamFrame && (
              <Button
                appearance="primary"
                icon={<PlayRegular />}
                onClick={() => onLaunchFrame(g)}
                disabled={!isConnected || isBusy}
              >
                Launch on Frame
              </Button>
            )}
            {!noSideload && (
              <Button
                appearance="primary"
                icon={<ArrowUpRegular />}
                onClick={() => onUpdate(g)}
                disabled={!isConnected || isBusy}
              >
                Update
              </Button>
            )}
            {!noSideload && (
              <Button
                appearance="secondary"
                style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
                icon={<UninstallIcon />}
                onClick={() => onUninstall(g)}
                disabled={!isConnected || isBusy}
              >
                Uninstall
              </Button>
            )}
            {noSideload && (
              <Text
                size={200}
                style={{ color: 'rgba(var(--vrcd-neon-raw),0.5)', fontFamily: 'monospace' }}
              >
                Sideloading disabled
              </Text>
            )}
          </div>
        )
      return (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {selectedDeviceDetails?.isSteamFrame && (
            <Button
              appearance="primary"
              icon={<PlayRegular />}
              onClick={() => onLaunchFrame(g)}
              disabled={!isConnected || isBusy}
            >
              Launch on Frame
            </Button>
          )}
          {!noSideload && !g.notOnServer && (
            <Button
              appearance="secondary"
              icon={<ArrowSyncRegular />}
              onClick={() => onReinstall(g)}
              disabled={!isConnected || isBusy}
            >
              Reinstall
            </Button>
          )}
          {!noSideload && (
            <Button
              appearance="secondary"
              style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
              icon={<UninstallIcon />}
              onClick={() => onUninstall(g)}
              disabled={!isConnected || isBusy}
            >
              Uninstall
            </Button>
          )}
          {noSideload && (
            <Text
              size={200}
              style={{ color: 'rgba(var(--vrcd-neon-raw),0.5)', fontFamily: 'monospace' }}
            >
              Sideloading disabled
            </Text>
          )}
        </div>
      )
    }
    if (isDownloaded)
      return (
        <div style={{ display: 'flex', gap: 8 }}>
          {!noSideload && (
            <Button
              appearance="primary"
              icon={<CheckmarkCircleRegular />}
              onClick={() => onInstallFromCompleted(g)}
              disabled={!isConnected || isBusy}
            >
              Install
            </Button>
          )}
          <Button
            appearance="secondary"
            style={{ color: '#ff5555', borderColor: 'rgba(255,85,85,0.5)' }}
            icon={<DeleteRegular />}
            onClick={() => onDeleteDownloaded(g)}
            disabled={isBusy}
          >
            Delete Files
          </Button>
        </div>
      )
    return (
      <Button
        appearance="primary"
        icon={<DownloadIcon />}
        onClick={() => onInstall(g)}
        disabled={isBusy}
      >
        Download
      </Button>
    )
  }

  if (!game) return null

  const statusEntry = game.releaseName ? downloadStatusMap.get(game.releaseName) : undefined
  const dlStatus = statusEntry?.status
  const dlProgress = statusEntry?.progress ?? 0
  const showProgress =
    dlStatus === 'Downloading' || dlStatus === 'Extracting' || dlStatus === 'Installing'

  const statusColor = game.isInstalled
    ? NEON
    : dlStatus === 'Completed'
      ? 'rgba(var(--vrcd-neon-raw),0.5)'
      : dlStatus === 'InstallError' || dlStatus === 'Error'
        ? '#ff5555'
        : 'rgba(var(--vrcd-neon-raw),0.4)'
  const statusLabel = game.isInstalled
    ? game.hasUpdate
      ? 'Update Available'
      : 'Installed'
    : dlStatus === 'Completed'
      ? 'Downloaded'
      : dlStatus === 'InstallError'
        ? isSignatureMismatchError(statusEntry?.error)
          ? 'Signature Mismatch'
          : 'Install Error'
        : dlStatus === 'Error'
          ? 'Download Error'
          : dlStatus === 'Installing'
            ? 'Installing...'
            : 'Not Installed'

  return (
    <Dialog
      open={open}
      onOpenChange={(_e, d) => !d.open && onClose()}
      modalType="modal"
      surfaceMotion={null}
    >
      <DialogSurface
        mountNode={document.getElementById('portal')}
        backdropMotion={null}
        style={{
          ...SURFACE_VARS,
          background: BG,
          border: `1px solid rgba(var(--vrcd-neon-raw),0.4)`,
          boxShadow: `0 0 50px rgba(var(--vrcd-neon-raw),0.08), 0 0 1px rgba(var(--vrcd-purple-raw),0.3)`,
          maxWidth: '680px',
          width: '90vw',
          maxHeight: '92vh',
          padding: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column'
        }}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: 10,
            right: 12,
            zIndex: 10,
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'rgba(var(--vrcd-neon-raw),0.6)',
            fontSize: 18,
            lineHeight: 1,
            padding: '2px 6px'
          }}
          aria-label="Close"
        >
          ✕
        </button>

        <DialogBody
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '20px 24px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16
          }}
        >
          {/* ── Cover + info row ── */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '140px 1fr',
              gap: 16,
              alignItems: 'start'
            }}
          >
            {/* Cover image */}
            <GameCoverLightbox
              src={game.thumbnailPath ? localFileUrl(game.thumbnailPath) : placeholderImage}
              alt={game.name}
              isPlaceholder={!game.thumbnailPath}
              loading={loadingDescription}
              labels={{ open: 'Enlarge cover', close: 'Close enlarged cover' }}
            />

            {/* Game meta */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div
                  style={{
                    flex: 1,
                    fontSize: 18,
                    fontWeight: 700,
                    color: NEON,
                    fontFamily: 'monospace',
                    letterSpacing: '0.04em',
                    lineHeight: 1.2
                  }}
                >
                  {game.name}
                </div>
                <Button
                  appearance="subtle"
                  size="small"
                  icon={isStarred ? <StarFilled /> : <StarRegular />}
                  aria-label={isStarred ? 'Unstar game' : 'Star game'}
                  title={isStarred ? 'Unstar game' : 'Star game'}
                  disabled={!game.packageName}
                  onClick={onToggleStarred}
                  style={{
                    minWidth: 28,
                    padding: 2,
                    color: isStarred ? NEON : 'rgba(var(--vrcd-neon-raw),0.55)'
                  }}
                />
              </div>
              <div style={{ fontSize: 11, color: `${PURPLE}cc`, fontFamily: 'monospace' }}>
                {game.packageName}
              </div>

              {/* Status badge */}
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  flexWrap: 'wrap',
                  marginTop: 2
                }}
              >
                {dlStatus === 'InstallError' || dlStatus === 'Error' ? (
                  <button
                    type="button"
                    onClick={() => setErrorDetailOpen(true)}
                    title="Click for error details"
                    style={{
                      fontSize: 11,
                      fontFamily: 'monospace',
                      fontWeight: 600,
                      color: statusColor,
                      border: `1px solid ${statusColor}`,
                      borderRadius: 4,
                      padding: '1px 7px',
                      letterSpacing: '0.06em',
                      background: 'transparent',
                      cursor: 'pointer'
                    }}
                  >
                    {statusLabel} ⓘ
                  </button>
                ) : (
                  <span
                    style={{
                      fontSize: 11,
                      fontFamily: 'monospace',
                      fontWeight: 600,
                      color: statusColor,
                      border: `1px solid ${statusColor}`,
                      borderRadius: 4,
                      padding: '1px 7px',
                      letterSpacing: '0.06em'
                    }}
                  >
                    {statusLabel}
                  </span>
                )}
                <span
                  style={{
                    fontSize: 11,
                    color: 'rgba(var(--vrcd-neon-raw),0.6)',
                    fontFamily: 'monospace'
                  }}
                >
                  <DocumentDataRegular
                    fontSize={12}
                    style={{ verticalAlign: 'middle', marginRight: 3 }}
                  />
                  {game.size || '-'}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: 'rgba(var(--vrcd-neon-raw),0.6)',
                    fontFamily: 'monospace'
                  }}
                >
                  <DownloadIcon fontSize={12} style={{ verticalAlign: 'middle', marginRight: 3 }} />
                  {game.downloads?.toLocaleString() || '-'}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: 'rgba(var(--vrcd-neon-raw),0.6)',
                    fontFamily: 'monospace'
                  }}
                >
                  <InfoRegular fontSize={12} style={{ verticalAlign: 'middle', marginRight: 3 }} />
                  {game.version ? `v${game.version}` : '-'}
                  {game.isInstalled && game.deviceVersionCode && (
                    <span style={{ color: 'rgba(var(--vrcd-neon-raw),0.4)' }}>
                      {' '}
                      (dev: v{game.deviceVersionCode})
                    </span>
                  )}
                </span>
              </div>

              <div
                style={{
                  height: '1px',
                  background: 'rgba(var(--vrcd-neon-raw),0.15)',
                  marginTop: 4
                }}
              />

              <div
                style={{
                  fontSize: 11,
                  color: 'rgba(var(--vrcd-neon-raw),0.7)',
                  fontFamily: 'monospace',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 3
                }}
              >
                <span>
                  <TagRegular fontSize={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                  {game.releaseName || '-'}
                </span>
                <span>
                  <CalendarClockRegular
                    fontSize={12}
                    style={{ verticalAlign: 'middle', marginRight: 4 }}
                  />
                  {String(game.lastUpdated || '-')}
                </span>
              </div>

              {/* ── ACTION BUTTONS (moved up) ── */}
              <div style={{ marginTop: 8 }}>{renderActionButtons(game)}</div>
            </div>
          </div>

          <GameDescriptionPanel
            loading={loadingDescription}
            result={descriptionResult}
            labels={{
              heading: 'DESCRIPTION',
              loading: 'Loading description…',
              unavailable: 'No description available.'
            }}
          />

          {/* Download progress */}
          {showProgress && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Spinner size="tiny" />
                <span style={{ color: NEON, fontFamily: 'monospace', fontSize: 12 }}>
                  {dlStatus}... {dlProgress}%
                </span>
              </div>
              <ProgressBar value={dlProgress} max={100} shape="rounded" thickness="medium" />
            </div>
          )}

          {/* ── Save Backup (BETA) controls ── */}
          {game.packageName && (
            <GameSaveBackupControls
              packageName={game.packageName}
              appLabel={game.name}
              isInstalled={!!game.isInstalled}
            />
          )}

          {/* ── Note section (bottom) ── */}
          <div style={{ borderTop: '1px solid rgba(var(--vrcd-neon-raw),0.12)', paddingTop: 12 }}>
            <div
              style={{
                fontSize: 11,
                fontFamily: 'monospace',
                letterSpacing: '0.1em',
                color: 'rgba(var(--vrcd-neon-raw),0.6)',
                marginBottom: 6
              }}
            >
              {'// NOTE'}
            </div>
            {loadingNote ? (
              <Spinner size="tiny" label="Loading..." />
            ) : currentGameNote ? (
              <NoteRenderer
                note={currentGameNote}
                selectedDevice={selectedDevice}
                downloadPath={statusEntry?.downloadPath ?? null}
              />
            ) : (
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 12,
                  color: 'rgba(var(--vrcd-neon-raw),0.35)'
                }}
              >
                No note available.
              </span>
            )}
          </div>
        </DialogBody>
      </DialogSurface>
      <ErrorDetailDialog
        open={errorDetailOpen}
        onClose={() => setErrorDetailOpen(false)}
        error={statusEntry?.error}
        phase={(dlStatus === 'InstallError' ? 'install' : 'download') as ErrorPhase}
        contextLabel={`${game.name}${game.releaseName ? ` (${game.releaseName})` : ''}`}
        onRetry={() => onRetry(game)}
      />
    </Dialog>
  )
}

export default GameDetailsDialog
