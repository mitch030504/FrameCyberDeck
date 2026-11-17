import { PackageInfo, ExtendedDeviceInfo, VentureStatus } from '@shared/types'
import { createContext } from 'react'

export interface AdbContextType {
  devices: ExtendedDeviceInfo[]
  selectedDevice: string | null
  selectedDeviceDetails: ExtendedDeviceInfo | null
  isConnected: boolean
  isLoading: boolean
  error: string | null
  packages: PackageInfo[]
  /** Latest external Venture CLI status; null until the first check completes. */
  ventureStatus: VentureStatus | null
  loadingPackages: boolean
  userName: string
  loadingUserName: boolean
  connectToDevice: (serial: string) => Promise<boolean>
  connectTcpDevice: (ipAddress: string, port?: number) => Promise<boolean>
  disconnectTcpDevice: (ipAddress: string, port?: number) => Promise<boolean>
  refreshDevices: () => Promise<void>
  refreshVentureStatus: (force?: boolean) => Promise<VentureStatus>
  disconnectDevice: () => void
  loadPackages: () => Promise<void>
  getUserName: () => Promise<string>
  setUserName: (name: string) => Promise<void>
  pingDevice: (ipAddress: string) => Promise<{ reachable: boolean; responseTime?: number }>
}

export const AdbContext = createContext<AdbContextType | undefined>(undefined)
