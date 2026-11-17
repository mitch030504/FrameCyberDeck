import type { ExtendedDeviceInfo, GameInfo } from '@shared/types'

export interface TargetCapabilities {
  isVenture: boolean
  supportsAdbShell: boolean
  supportsObbCopy: boolean
  supportsLaunch: boolean
  installVerb: 'Install' | 'Import'
  refreshLabel: string
}

/**
 * Central place for the handful of target-specific UI differences. Venture is
 * a PC library managed through an external CLI, so ADB shell, OBB copying, and
 * sideload wording do not apply to it.
 */
/**
 * Venture accepts its opaque library ID for play/uninstall; Android targets
 * use the package name. Never substitute one for the other when a Venture ID
 * is known, because the two can differ and IDs may contain ':'.
 */
export function getTargetGameId(game: Pick<GameInfo, 'ventureId' | 'packageName'>): string {
  return game.ventureId ?? game.packageName
}

export function getTargetCapabilities(
  device: Pick<ExtendedDeviceInfo, 'isVenture' | 'isSteamFrame'> | null | undefined
): TargetCapabilities {
  if (device?.isVenture) {
    return {
      isVenture: true,
      supportsAdbShell: false,
      supportsObbCopy: false,
      supportsLaunch: true,
      installVerb: 'Import',
      refreshLabel: 'Refresh PC Library'
    }
  }

  return {
    isVenture: false,
    supportsAdbShell: true,
    supportsObbCopy: true,
    supportsLaunch: Boolean(device?.isSteamFrame),
    installVerb: 'Install',
    refreshLabel: device?.isSteamFrame ? 'Refresh Frame Titles' : 'Refresh Quest'
  }
}
