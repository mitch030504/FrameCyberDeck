import { app } from 'electron'
import { existsSync } from 'fs'
import { join } from 'path'
import frameDevkitService from './frameDevkitService'

/**
 * Commands this app is allowed to run on the Frame. The vendored agent can do
 * much more (Steam shortcuts, Lepton installs, diagnostics); those stay off
 * because our devkit service owns deployment and launching.
 */
const ALLOWED_COMMANDS = new Set([
  'info',
  'battery',
  'room_status',
  'room_capture',
  'room_capture_status',
  'room_capture_stop',
  'room_put',
  'room_delete',
  'room_from_boundary',
  'room_build',
  'room_refine',
  'set_settings',
  'video_codec_status',
  'video_codec_switch'
])

export type FrameAgentResult = Record<string, unknown> & { ok: boolean }

class FrameAgentService {
  private synced = new Set<string>()

  getAgentPath(): string {
    return app.isPackaged
      ? join(process.resourcesPath, 'vendor', 'frameport', 'agent', 'frameport_agent.py')
      : join(app.getAppPath(), 'vendor', 'frameport', 'agent', 'frameport_agent.py')
  }

  async ensureAgent(host?: string): Promise<void> {
    const path = this.getAgentPath()
    if (!existsSync(path)) throw new Error(`Vendored FramePort agent is missing: ${path}`)
    const key = host ?? 'default'
    if (this.synced.has(key)) return
    const result = await frameDevkitService.syncFramePortAgent(path, host)
    if (!result.ok) throw new Error('Uploading the FramePort agent failed')
    this.synced.add(key)
  }

  async run(
    command: string,
    args: Record<string, unknown> = {},
    host?: string
  ): Promise<FrameAgentResult> {
    if (!ALLOWED_COMMANDS.has(command)) {
      throw new Error(`Frame agent command is not allowed: ${command}`)
    }
    await this.ensureAgent(host)
    const reply = await frameDevkitService.runFramePortAgent(command, args, host)
    const result = reply.result
    if (result && typeof result === 'object' && !Array.isArray(result)) {
      return { ...(result as Record<string, unknown>), ok: Boolean(reply.ok) }
    }
    return reply
  }

  async info(host?: string): Promise<FrameAgentResult> {
    return await this.run('info', { proton: false }, host)
  }
}

export default new FrameAgentService()
