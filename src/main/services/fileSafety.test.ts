import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { childPath, containedFile, writeJsonAtomic } from './fileSafety'

let root: string
beforeEach(async () => {
  root = await fs.mkdtemp(join(tmpdir(), 'frame-safety-'))
})
afterEach(async () => {
  vi.restoreAllMocks()
  await fs.rm(root, { recursive: true, force: true })
})

describe('filesystem boundaries', () => {
  it.each([
    '',
    '.',
    '..',
    '.. ',
    '../private',
    'a/b',
    'a\\b',
    '/tmp',
    'C:\\private',
    'game.',
    'game\0',
    'game\u001f'
  ])('rejects folder name %j', (name) => {
    expect(() => childPath(root, name)).toThrow()
  })

  it('accepts a normal release name', () => {
    expect(childPath(root, 'Game v1.2 (release)')).toBe(join(root, 'Game v1.2 (release)'))
  })

  it('rejects traversal and symlinks leaving the release', async () => {
    const release = join(root, 'release')
    await fs.mkdir(release)
    await fs.writeFile(join(root, 'private.txt'), 'secret')
    await fs.symlink(join(root, 'private.txt'), join(release, 'link'))
    await expect(containedFile(release, '../private.txt')).rejects.toThrow()
    await expect(containedFile(release, 'link')).rejects.toThrow()
    await fs.writeFile(join(release, 'game.apk'), 'apk')
    expect(await containedFile(release, '.\\game.apk')).toBe(join(release, 'game.apk'))
  })

  it('keeps the previous JSON if the atomic replacement fails', async () => {
    const path = join(root, 'queue.json')
    await writeJsonAtomic(path, { revision: 1 })
    vi.spyOn(fs, 'rename').mockRejectedValue(new Error('replacement failed'))
    await expect(writeJsonAtomic(path, { revision: 2 })).rejects.toThrow('replacement failed')
    expect(JSON.parse(await fs.readFile(path, 'utf8'))).toEqual({ revision: 1 })
    expect(await fs.readdir(root)).toEqual(['queue.json'])
  })

  it('serializes atomic writes and leaves complete JSON without temporary files', async () => {
    const path = join(root, 'queue.json')
    await Promise.all(
      Array.from({ length: 20 }, (_, revision) => writeJsonAtomic(path, { revision }))
    )
    expect(JSON.parse(await fs.readFile(path, 'utf8'))).toEqual({ revision: 19 })
    expect(await fs.readdir(root)).toEqual(['queue.json'])
  })
})
