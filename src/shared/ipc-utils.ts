import { ipcRenderer, IpcRendererEvent, ipcMain, BrowserWindow } from 'electron'
import { IPCChannels, IPCSendChannels, IPCEvents } from './types/ipc'

let trustedSender: (event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent) => boolean = () =>
  false

export function setIpcSenderValidator(validator: typeof trustedSender): void {
  trustedSender = validator
}

// Type-safe wrappers for renderer process (preload)
export const typedIpcRenderer = {
  // Type-safe invoke (with response)
  invoke: <K extends keyof IPCChannels>(
    channel: K,
    ...args: IPCChannels[K]['params']
  ): Promise<IPCChannels[K]['returns']> => {
    return ipcRenderer.invoke(channel, ...args)
  },

  // Type-safe send (no response)
  send: <K extends keyof IPCSendChannels>(
    channel: K,
    ...args: IPCSendChannels[K] extends void ? [] : [IPCSendChannels[K]]
  ): void => {
    ipcRenderer.send(channel, ...args)
  },

  // Type-safe event listeners
  on: <K extends keyof IPCEvents>(
    channel: K,
    listener: (event: IpcRendererEvent, ...args: IPCEvents[K]) => void
  ): void => {
    ipcRenderer.on(channel, listener)
  },

  removeListener: <K extends keyof IPCEvents>(
    channel: K,
    listener: (event: IpcRendererEvent, ...args: IPCEvents[K]) => void
  ): void => {
    ipcRenderer.removeListener(channel, listener)
  }
}

// Type-safe wrappers for main process
export const typedIpcMain = {
  // Type-safe handler registration
  handle: <K extends keyof IPCChannels>(
    channel: K,
    handler: (
      event: Electron.IpcMainInvokeEvent,
      ...args: IPCChannels[K]['params']
    ) => Promise<IPCChannels[K]['returns']> | IPCChannels[K]['returns']
  ): void => {
    ipcMain.handle(channel, (event, ...args: IPCChannels[K]['params']) => {
      if (!trustedSender(event)) throw new Error('Untrusted IPC sender')
      return handler(event, ...args)
    })
  },

  // Type-safe event handler registration
  on: <K extends keyof IPCSendChannels>(
    channel: K,
    listener: (
      event: Electron.IpcMainEvent,
      ...args: IPCSendChannels[K] extends void ? [] : [IPCSendChannels[K]]
    ) => void
  ): void => {
    ipcMain.on(
      channel,
      (event, ...args: IPCSendChannels[K] extends void ? [] : [IPCSendChannels[K]]) => {
        if (trustedSender(event)) listener(event, ...args)
      }
    )
  }
}

// Type-safe wrapper for WebContents.send
export const typedWebContentsSend = {
  // Type-safe send for WebContents
  send: <K extends keyof IPCEvents>(
    window: BrowserWindow,
    channel: K,
    ...args: IPCEvents[K]
  ): void => {
    window.webContents.send(channel, ...args)
  }
}
