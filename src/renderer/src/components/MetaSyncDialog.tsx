import React, { useCallback, useEffect, useState } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Text,
  Input,
  Checkbox,
  makeStyles,
  tokens
} from '@fluentui/react-components'
import {
  PersonCircleRegular,
  ArrowSyncRegular,
  DismissRegular,
  SendRegular
} from '@fluentui/react-icons'
import { MetaAccountStatus, MetaSyncCandidate, MetaSyncProgress } from '@shared/types'

const useStyles = makeStyles({
  description: {
    marginBottom: tokens.spacingVerticalM
  },
  actionRow: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
    marginTop: tokens.spacingVerticalM,
    marginBottom: tokens.spacingVerticalM
  },
  errorBox: {
    marginTop: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalM,
    backgroundColor: tokens.colorPaletteRedBackground1,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorPaletteRedBorder1}`
  },
  candidateList: {
    marginTop: tokens.spacingVerticalM,
    maxHeight: '260px',
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingHorizontalXS
  },
  candidateRow: {
    display: 'flex',
    alignItems: 'center',
    gap: `${tokens.spacingHorizontalS}px`
  },
  progressLine: {
    fontFamily: 'var(--vrcd-font-mono)',
    fontSize: '11px',
    color: 'rgba(var(--vrcd-neon-raw),0.75)'
  },
  emptyState: {
    marginTop: tokens.spacingVerticalM,
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic'
  }
})

const surfaceStyle: React.CSSProperties = {
  maxWidth: '680px',
  width: '90vw',
  background: '#050514',
  border: '1px solid rgba(var(--vrcd-neon-raw),0.35)',
  ['--colorNeutralForeground1' as string]: 'var(--vrcd-neon)',
  ['--colorNeutralForeground2' as string]: 'rgba(var(--vrcd-neon-raw),0.75)',
  ['--colorNeutralBackground1' as string]: '#050514',
  ['--colorNeutralStroke1' as string]: 'rgba(var(--vrcd-neon-raw),0.25)',
  ['--colorBrandBackground' as string]: 'var(--vrcd-neon)',
  ['--colorNeutralForegroundOnBrand' as string]: '#050514'
}

const buttonStyle: React.CSSProperties = {
  fontFamily: 'var(--vrcd-font-mono)',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  fontSize: '11px',
  color: 'var(--vrcd-neon)',
  justifyContent: 'flex-start',
  border: '1px solid rgba(var(--vrcd-neon-raw),0.45)'
}

const MetaSyncDialog: React.FC = () => {
  const styles = useStyles()

  const [isOpen, setIsOpen] = useState(false)
  const [account, setAccount] = useState<MetaAccountStatus | null>(null)
  const [tokenInput, setTokenInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<MetaSyncCandidate[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [isChecking, setIsChecking] = useState(false)
  const [hasChecked, setHasChecked] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [progressByPackage, setProgressByPackage] = useState<Record<string, MetaSyncProgress>>({})

  const refreshAccount = useCallback(async (): Promise<void> => {
    try {
      setAccount(await window.api.metaSync.getAccountStatus())
    } catch {
      setAccount({ authenticated: false })
    }
  }, [])

  useEffect(() => {
    if (isOpen) void refreshAccount()
  }, [isOpen, refreshAccount])

  useEffect(() => {
    const unsubscribe = window.api.metaSync.onSyncProgress((progress) => {
      setProgressByPackage((prev) => ({ ...prev, [progress.packageName]: progress }))
    })
    return unsubscribe
  }, [])

  const handleLoginSso = async (): Promise<void> => {
    setError(null)
    const result = await window.api.metaSync.loginWithSso()
    if (result.ok) {
      await refreshAccount()
    } else {
      setError(result.error ?? 'Sign-in failed')
    }
  }

  const handleLoginToken = async (): Promise<void> => {
    setError(null)
    const result = await window.api.metaSync.loginWithToken(tokenInput)
    if (result.ok) {
      setTokenInput('')
      await refreshAccount()
    } else {
      setError(result.error ?? 'Token validation failed')
    }
  }

  const handleLogout = async (): Promise<void> => {
    await window.api.metaSync.logout()
    setAccount({ authenticated: false })
    setCandidates([])
    setSelected(new Set())
  }

  const handleFindUpdates = async (): Promise<void> => {
    setIsChecking(true)
    setHasChecked(false)
    setError(null)
    setCandidates([])
    try {
      const result = await window.api.metaSync.findUpdates()
      if (result.error) {
        setError(result.error)
      } else {
        setCandidates(result.candidates)
        setSelected(new Set(result.candidates.map((candidate) => candidate.packageName)))
        setHasChecked(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Check failed')
    } finally {
      setIsChecking(false)
    }
  }

  const handleQueueSelected = async (): Promise<void> => {
    const chosen = candidates.filter((candidate) => selected.has(candidate.packageName))
    if (chosen.length === 0 || isSyncing) return
    setIsSyncing(true)
    setError(null)
    try {
      await window.api.metaSync.queueUpdates(chosen)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sync failed')
    } finally {
      setIsSyncing(false)
    }
  }

  const toggleSelected = (packageName: string): void => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(packageName)) next.delete(packageName)
      else next.add(packageName)
      return next
    })
  }

  const authenticated = account?.authenticated ?? false
  const hasQueued = candidates.some(
    (candidate) => progressByPackage[candidate.packageName]?.percent === 100
  )

  return (
    <>
      <Button
        icon={<ArrowSyncRegular />}
        appearance="subtle"
        size="small"
        onClick={() => setIsOpen(true)}
        style={buttonStyle}
      >
        {'Sync Meta Library'}
      </Button>

      <Dialog
        open={isOpen}
        onOpenChange={(_, data) => {
          if (!data.open) setIsOpen(false)
        }}
      >
        <DialogSurface mountNode={document.getElementById('portal')} style={surfaceStyle}>
          <DialogBody>
            <DialogTitle>{'Meta Library Sync'}</DialogTitle>
            <DialogContent>
              <Text className={styles.description}>
                {
                  'Sign in with the Meta account that owns your Quest titles. New titles and updates newer than the ones on the server are downloaded from Meta and pushed through the regular upload pipeline.'
                }
              </Text>

              {authenticated ? (
                <div className={styles.actionRow}>
                  <Text>
                    {'Signed in'}
                    {account?.name ? ` as ${account.name}` : ''}
                  </Text>
                  <Button size="small" icon={<DismissRegular />} onClick={handleLogout}>
                    {'Sign out'}
                  </Button>
                </div>
              ) : (
                <>
                  <div className={styles.actionRow}>
                    <Button icon={<PersonCircleRegular />} onClick={handleLoginSso}>
                      {'Sign in with Meta'}
                    </Button>
                  </div>
                  <Text className={styles.description}>
                    {
                      'If the sign-in window does not work in your environment, paste a Meta user token (starts with FRL) instead.'
                    }
                  </Text>
                  <div className={styles.actionRow}>
                    <Input
                      type="password"
                      placeholder="FRL…"
                      value={tokenInput}
                      onChange={(_, data) => setTokenInput(data.value)}
                      style={{ flexGrow: 1 }}
                    />
                    <Button
                      disabled={!tokenInput.trim()}
                      icon={<SendRegular />}
                      onClick={handleLoginToken}
                    >
                      {'Use token'}
                    </Button>
                  </div>
                </>
              )}

              {error && (
                <div className={styles.errorBox}>
                  <Text>{error}</Text>
                </div>
              )}

              {authenticated && (
                <div className={styles.actionRow}>
                  <Button
                    icon={<ArrowSyncRegular />}
                    onClick={handleFindUpdates}
                    disabled={isChecking || isSyncing}
                  >
                    {isChecking ? 'Checking…' : 'Check for new titles / updates'}
                  </Button>
                </div>
              )}

              {candidates.length > 0 && (
                <div className={styles.candidateList}>
                  {candidates.map((candidate) => {
                    const progress = progressByPackage[candidate.packageName]
                    return (
                      <div key={candidate.packageName} className={styles.candidateRow}>
                        <Checkbox
                          checked={selected.has(candidate.packageName)}
                          onChange={() => toggleSelected(candidate.packageName)}
                          disabled={progress?.percent === 100}
                        />
                        <div style={{ flexGrow: 1 }}>
                          <Text>
                            {candidate.displayName}
                            {candidate.kind === 'update' && candidate.serverVersionCode
                              ? ` (server v${candidate.serverVersionCode} → v${candidate.versionCode})`
                              : ` (new, v${candidate.versionCode})`}
                          </Text>
                          {progress && (
                            <div className={styles.progressLine}>
                              {progress.error
                                ? `${progress.stage}: ${progress.error}`
                                : `${progress.stage} ${progress.percent}%`}
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}

              {authenticated && hasChecked && !isChecking && candidates.length === 0 && (
                <div className={styles.emptyState}>
                  <Text size={200}>{'Everything you own is already on the server.'}</Text>
                </div>
              )}
            </DialogContent>
            <DialogActions>
              <Button
                appearance="primary"
                disabled={selected.size === 0 || isSyncing || isChecking || hasQueued}
                onClick={handleQueueSelected}
              >
                {isSyncing ? 'Syncing…' : `Download & queue (${selected.size})`}
              </Button>
              <Button appearance="subtle" onClick={() => setIsOpen(false)}>
                {'Close'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </>
  )
}

export default MetaSyncDialog
