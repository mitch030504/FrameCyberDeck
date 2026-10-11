import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/frame-dependency-tests', getAppPath: () => process.cwd() }
}))
vi.mock('axios', () => ({ default: { get: vi.fn().mockRejectedValue(new Error('offline')) } }))
vi.mock('child_process', () => ({ execSync: vi.fn().mockReturnValue('rclone v1.72.1\n') }))

import dependencies from './dependencyService'
import axios from 'axios'

let root: string
beforeEach(async () => {
  root = await fs.mkdtemp(join(tmpdir(), 'frame-dependencies-'))
  dependencies['binDir'] = root
  dependencies['serviceStatus'] = 'NOT_INITIALIZED'
})
afterEach(async () => {
  vi.restoreAllMocks()
  await fs.rm(root, { recursive: true, force: true })
})

it('initializes offline when the local tools are present', async () => {
  await fs.writeFile(dependencies.getRclonePath(), 'rclone')
  await fs.writeFile(dependencies.getAdbPath(), 'adb')
  const check = dependencies['checkBundled7zip']
  dependencies['checkBundled7zip'] = () => {
    dependencies['status'].sevenZip.ready = true
  }
  try {
    expect(await dependencies.initialize()).toBe('INITIALIZED')
  } finally {
    dependencies['checkBundled7zip'] = check
  }
  expect(axios.get).not.toHaveBeenCalled()
})

it('leaves the initialization state after a failure so a retry can run', async () => {
  const check = dependencies['checkBundled7zip']
  const failing = vi.fn(() => {
    throw new Error('missing bundled tool')
  })
  dependencies['checkBundled7zip'] = failing
  try {
    await expect(dependencies.initialize()).rejects.toThrow('missing bundled tool')
    await expect(dependencies.initialize()).rejects.toThrow('missing bundled tool')
    expect(failing).toHaveBeenCalledTimes(2)
    expect(dependencies['serviceStatus']).toBe('ERROR')
  } finally {
    dependencies['checkBundled7zip'] = check
  }
})
