import { promises as fs } from 'node:fs'
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path'
import { randomUUID } from 'node:crypto'

export function childPath(root: string, name: string): string {
  if (
    !name ||
    name === '.' ||
    name === '..' ||
    /[. ]$/.test(name) ||
    // eslint-disable-next-line no-control-regex -- Reject control bytes in untrusted folder names.
    /[\\/\x00-\x1f<>:"|?*]/.test(name)
  ) {
    throw new Error('Invalid folder name')
  }
  return resolve(root, name)
}

export async function containedFile(root: string, name: string): Promise<string> {
  const realRoot = await fs.realpath(root)
  const candidate = await fs.realpath(resolve(root, name.replace(/\\/g, '/')))
  const fromRoot = relative(realRoot, candidate)
  if (fromRoot === '..' || fromRoot.startsWith(`..${sep}`) || isAbsolute(fromRoot)) {
    throw new Error('Install script path leaves the release folder')
  }
  return candidate
}

const writes = new Map<string, Promise<void>>()

export function writeJsonAtomic(path: string, value: unknown): Promise<void> {
  const data = JSON.stringify(value, null, 2)
  const previous = writes.get(path) ?? Promise.resolve()
  const write = previous
    .catch(() => {})
    .then(async () => {
      const temporary = `${path}.${randomUUID()}.tmp`
      try {
        await fs.mkdir(dirname(path), { recursive: true })
        const handle = await fs.open(temporary, 'wx', 0o600)
        try {
          await handle.writeFile(data, 'utf-8')
          await handle.sync()
        } finally {
          await handle.close()
        }
        await fs.rename(temporary, path)
      } finally {
        await fs.rm(temporary, { force: true })
      }
    })
  writes.set(path, write)
  void write
    .finally(() => {
      if (writes.get(path) === write) writes.delete(path)
    })
    .catch(() => {})
  return write
}
