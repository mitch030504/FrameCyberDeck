import { expect, it, vi } from 'vitest'
vi.mock('electron', () => ({ ipcMain: { handle: vi.fn(), on: vi.fn() }, ipcRenderer: {} }))
import { ipcMain } from 'electron'
import { setIpcSenderValidator, typedIpcMain } from './ipc-utils'

it('rejects invoke and send messages unless the main process trusts the sender', () => {
  let trusted = false
  setIpcSenderValidator(() => trusted)
  const invoke = vi.fn(() => 'version')
  const send = vi.fn()
  typedIpcMain.handle('app:get-version', invoke)
  typedIpcMain.on('app:confirm-close', send)
  const handle = vi.mocked(ipcMain.handle).mock.calls[0][1]
  const listener = vi.mocked(ipcMain.on).mock.calls[0][1]
  const event = {} as Electron.IpcMainInvokeEvent
  expect(() => handle(event)).toThrow('Untrusted IPC sender')
  listener({} as Electron.IpcMainEvent)
  expect(invoke).not.toHaveBeenCalled()
  expect(send).not.toHaveBeenCalled()
  trusted = true
  expect(handle(event)).toBe('version')
  listener({} as Electron.IpcMainEvent)
  expect(send).toHaveBeenCalledOnce()
})
