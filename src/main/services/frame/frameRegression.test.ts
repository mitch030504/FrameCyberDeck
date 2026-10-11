import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const paths = vi.hoisted(() => ({ root: '/tmp/frame-regression' }))
vi.mock('electron', () => ({ app: { getPath: () => paths.root, getAppPath: () => process.cwd() } }))
vi.mock('../adbService', () => ({ default: {} }))
vi.mock('../settingsService', () => ({ default: {} }))

import conversion from './frameConversionService'
import devkit from './frameDevkitService'

beforeEach(async () => {
  paths.root = await fs.mkdtemp(join(tmpdir(), 'frame-deploy-test-'))
})
afterEach(async () => {
  vi.restoreAllMocks()
  await fs.rm(paths.root, { recursive: true, force: true })
})

it('serializes conversions, including after a failed conversion', async () => {
  let active = 0
  let peak = 0
  const original = conversion['convertNow']
  conversion['convertNow'] = vi.fn(async () => {
    peak = Math.max(peak, ++active)
    await new Promise((resolve) => setTimeout(resolve, 10))
    active--
    throw new Error('simulated conversion failure')
  })
  try {
    const results = await Promise.allSettled([
      conversion.convert('/one.apk', 'one'),
      conversion.convert('/two.apk', 'two')
    ])
    expect(results.map((result) => result.status)).toEqual(['rejected', 'rejected'])
    expect(peak).toBe(1)
  } finally {
    conversion['convertNow'] = original
  }
})

it('uses the verified package for the Devkit name even for generic game.apk titles', async () => {
  const ids: string[] = []
  for (const packageName of ['com.test.one', 'com.test.two']) {
    const source = join(paths.root, packageName)
    await fs.mkdir(source)
    await fs.writeFile(join(source, 'game.apk'), 'converted')
    await fs.writeFile(
      join(source, 'frame-conversion.json'),
      JSON.stringify({ package: packageName, versionCode: 42 })
    )
    const prepared = await devkit['createStagingDirectory'](source, 'game')
    ids.push(prepared.devkitName)
    expect(prepared.packageName).toBe(packageName)
    expect(prepared.versionCode).toBe(42)
  }
  expect(ids).toEqual(['com.test.one', 'com.test.two'])
})

it('rejects converted payloads without a package identity', async () => {
  const source = join(paths.root, 'missing')
  await fs.mkdir(source)
  await fs.writeFile(join(source, 'game.apk'), 'converted')
  await expect(devkit['createStagingDirectory'](source, 'game')).rejects.toThrow(
    'no valid package identity'
  )
})

it('stages non-OBB bundle data from obb/ with its folder structure', async () => {
  const source = join(paths.root, 'converted')
  await fs.mkdir(source)
  await fs.writeFile(join(source, 'game.apk'), 'converted')
  await fs.writeFile(
    join(source, 'frame-conversion.json'),
    JSON.stringify({ package: 'com.test.game', versionCode: 42 })
  )
  await fs.mkdir(join(source, 'obb', 'sub'), { recursive: true })
  await fs.writeFile(join(source, 'obb', 'data.bundle'), 'bundle')
  await fs.writeFile(join(source, 'obb', 'sub', 'extra.bin'), 'extra')
  const prepared = await devkit['createStagingDirectory'](source, 'game')
  const staged = join(prepared.directory, 'obb')
  expect(await fs.readFile(join(staged, 'data.bundle'), 'utf-8')).toBe('bundle')
  expect(await fs.readFile(join(staged, 'sub', 'extra.bin'), 'utf-8')).toBe('extra')
})

it('records matching original OBB paths for single APK installs', async () => {
  const source = join(paths.root, 'converted')
  await fs.mkdir(source)
  await fs.writeFile(join(source, 'game.apk'), 'converted')
  await fs.writeFile(
    join(source, 'frame-conversion.json'),
    JSON.stringify({ package: 'com.test.game', versionCode: 42 })
  )
  const original = join(paths.root, 'original.apk')
  await fs.writeFile(original, 'original')
  const obb = join(paths.root, 'main.42.com.test.game.obb')
  await fs.writeFile(obb, 'obb')
  await fs.writeFile(join(paths.root, 'main.1.com.other.game.obb'), 'other')
  vi.spyOn(devkit, 'status').mockResolvedValue({ ok: true, host: 'frame', paired: true })
  vi.spyOn(devkit, 'listGames').mockResolvedValue([])
  const bridge = devkit['bridge']
  devkit['bridge'] = vi.fn().mockResolvedValue({ ok: true })
  try {
    expect(await devkit.deploy(source, 'game', undefined, undefined, original)).toBe(true)
  } finally {
    devkit['bridge'] = bridge
  }
  expect(await devkit.getOriginalSource('com.test.game')).toMatchObject({
    originalSourcePath: original,
    originalObbPaths: [obb],
    versionCode: 42
  })
})

it.each([true, false])(
  'only preserves a legacy shortcut ID belonging to the package (owned=%s)',
  async (owned) => {
    const source = join(paths.root, 'converted')
    await fs.mkdir(source)
    await fs.writeFile(join(source, 'game.apk'), 'converted')
    await fs.writeFile(
      join(source, 'frame-conversion.json'),
      JSON.stringify({ package: 'com.test.game', versionCode: 42 })
    )
    vi.spyOn(devkit, 'status').mockResolvedValue({ ok: true, host: 'frame', paired: true })
    vi.spyOn(devkit, 'listGames').mockResolvedValue([
      {
        gameid: 'Old_Title',
        packageName: owned ? 'com.test.game' : 'com.other.game',
        versionCode: 41,
        managed: true
      }
    ])
    const original = devkit['bridge']
    const bridge = vi.fn().mockResolvedValue({ ok: true })
    devkit['bridge'] = bridge
    try {
      expect(await devkit.deploy(source, 'New Title')).toBe(true)
    } finally {
      devkit['bridge'] = original
    }
    expect(bridge.mock.calls[0][1]).toContain(owned ? 'Old_Title' : 'com.test.game')
  }
)

it('refuses to overwrite a foreign title occupying the package ID', async () => {
  const source = join(paths.root, 'converted')
  await fs.mkdir(source)
  await fs.writeFile(join(source, 'game.apk'), 'converted')
  await fs.writeFile(
    join(source, 'frame-conversion.json'),
    JSON.stringify({ package: 'com.test.game', versionCode: 42 })
  )
  vi.spyOn(devkit, 'status').mockResolvedValue({ ok: true, host: 'frame', paired: true })
  vi.spyOn(devkit, 'listGames').mockResolvedValue([
    { gameid: 'com.test.game', packageName: 'com.other.game', versionCode: 1, managed: true }
  ])
  await expect(devkit.deploy(source, 'Game')).rejects.toThrow('refusing to overwrite')
  expect(await fs.readdir(join(paths.root, 'frame-cyberdeck'))).toEqual([])
})
