import { promises as fs, createWriteStream } from 'fs'
import { join } from 'path'
import { pipeline } from 'stream/promises'
import { Readable } from 'stream'
import { BrowserWindow } from 'electron'
import { typedWebContentsSend } from '@shared/ipc-utils'
import metaAccountService, { graphRequest } from './metaAccountService'
import gameService from './gameService'
import settingsService from './settingsService'
import uploadService from './uploadService'
import { MetaSyncCandidate, MetaSyncProgress } from '@shared/types'

function emitProgress(progress: MetaSyncProgress): void {
  const mainWindow = BrowserWindow.getAllWindows()[0]
  if (mainWindow && !mainWindow.isDestroyed()) {
    typedWebContentsSend.send(mainWindow, 'meta-sync:progress', progress)
  }
}

// Persisted query ids used by Meta's own web clients; the shapes are the ones
// the community tooling (OculusGraphQLApiLib) has stable access to.
const DOC_ENTITLEMENTS = '4850747515044496'
const DOC_VERSION_HISTORY = '1586217024733717'
const DOC_BINARY_DETAILS = '4734929166632773'
const DOWNLOAD_URI = 'https://securecdn.oculus.com/binaries/download/'

interface EntitlementsResponse {
  data: { viewer: { user?: { active_entitlements?: { nodes?: EntitlementNode[] } } } }
}
interface EntitlementNode {
  item: { id: string; display_name?: string; canonical_name?: string }
}
interface VersionHistoryResponse {
  data: { node: { supportedBinaries: { edges: { node: BinaryNode }[] } } }
}
interface BinaryNode {
  id: string
  version: string
  version_code?: number
  versionCode?: number
  platform: string
  package_name?: string
}
interface BinaryDetailsResponse {
  data?: {
    node?: {
      obb_binary?: { id: string; file_name?: string } | null
      asset_files?: { nodes: { id: string; file_name?: string }[] }
    }
  }
}

export interface LatestVersion {
  versionCode: number
  version: string
  binaryId: string
  packageName: string
}

/**
 * Pick, from everything the account is entitled to, the titles that are new
 * or newer than what the server catalog already offers. Pure so it can be
 * tested without touching Meta or the network.
 */
export function selectCandidates(
  entitledApps: { appId: string; displayName: string }[],
  latestVersions: Record<string, LatestVersion>,
  catalog: { packageName: string; versionCode: number }[],
  isBlacklisted: (packageName: string, versionCode: number) => boolean
): MetaSyncCandidate[] {
  const serverVersionByPackage = new Map(
    catalog.map((entry) => [entry.packageName, entry.versionCode])
  )
  const candidates: MetaSyncCandidate[] = []
  for (const app of entitledApps) {
    const latest = latestVersions[app.appId]
    if (!latest) continue
    const serverVersion = serverVersionByPackage.get(latest.packageName)
    if (serverVersion !== undefined && latest.versionCode <= serverVersion) continue
    if (isBlacklisted(latest.packageName, latest.versionCode)) continue
    candidates.push({
      packageName: latest.packageName,
      appId: app.appId,
      displayName: app.displayName,
      versionCode: latest.versionCode,
      version: latest.version,
      binaryId: latest.binaryId,
      kind: serverVersion === undefined ? 'new' : 'update',
      serverVersionCode: serverVersion
    })
  }
  return candidates
}

class MetaSyncService {
  private syncing = false

  private async latestAndroidVersion(appId: string, token: string): Promise<LatestVersion | null> {
    const history = await graphRequest<VersionHistoryResponse>(
      DOC_VERSION_HISTORY,
      { id: appId },
      token
    )
    const edges = history.data.node?.supportedBinaries?.edges
    const binaries = (Array.isArray(edges) ? edges : [])
      .map((edge) => edge.node)
      .filter(
        (node) => node.platform?.startsWith('ANDROID') && (node.version_code || node.versionCode)
      )
    if (binaries.length === 0) return null
    const latest = binaries.reduce((a, b) =>
      (b.version_code ?? b.versionCode ?? 0) > (a.version_code ?? a.versionCode ?? 0) ? b : a
    )
    if (!latest.package_name) return null
    return {
      versionCode: latest.version_code ?? latest.versionCode ?? 0,
      version: latest.version ?? '',
      binaryId: latest.id,
      packageName: latest.package_name
    }
  }

  async findUpdates(): Promise<MetaSyncCandidate[]> {
    if (this.syncing) throw new Error('A Meta sync is already running')
    const token = await metaAccountService.getToken()
    if (!token) throw new Error('Sign in to your Meta account first')

    const entitlements = await graphRequest<EntitlementsResponse>(DOC_ENTITLEMENTS, {}, token)
    const nodes = entitlements.data.viewer.user?.active_entitlements?.nodes
    if (!Array.isArray(nodes)) {
      throw new Error('Meta returned no entitlement list for this account')
    }
    const entitledApps = nodes.map((node) => ({
      appId: node.item.id,
      displayName: node.item.display_name || node.item.canonical_name || node.item.id
    }))

    const latestVersions: Record<string, LatestVersion> = {}
    // Small bounded concurrency: a large library is ~100 apps.
    const queue = [...entitledApps]
    const workers = Array.from({ length: 4 }, async () => {
      for (let app = queue.shift(); app; app = queue.shift()) {
        try {
          const latest = await this.latestAndroidVersion(app.appId, token)
          if (latest) latestVersions[app.appId] = latest
        } catch (error) {
          console.warn(`[MetaSync] Could not resolve versions for ${app.displayName}:`, error)
        }
      }
    })
    await Promise.all(workers)

    const catalog = (await gameService.getGames())
      .map((game) => ({ packageName: game.packageName, versionCode: Number(game.version) || 0 }))
      .filter((entry) => entry.packageName)
    return selectCandidates(entitledApps, latestVersions, catalog, (packageName, versionCode) =>
      gameService.isGameBlacklisted(packageName, versionCode)
    )
  }

  private async downloadBinary(id: string, destination: string, token: string): Promise<void> {
    // The token is a query parameter; never log this URL.
    const response = await fetch(
      `${DOWNLOAD_URI}?id=${id}&access_token=${encodeURIComponent(token)}`,
      { headers: { 'User-Agent': 'OculusGraphQLApiLib/1.0' } }
    )
    if (!response.ok || !response.body) {
      throw new Error(`Download failed (${response.status})`)
    }
    await pipeline(Readable.fromWeb(response.body as never), createWriteStream(destination))
  }

  private async pathExists(path: string): Promise<boolean> {
    try {
      await fs.access(path)
      return true
    } catch {
      return false
    }
  }

  /**
   * Download each candidate from Meta's entitled CDN into the uploads cache
   * and hand it to the existing upload pipeline as a ready local item.
   */
  async queueUpdates(candidates: MetaSyncCandidate[]): Promise<number> {
    if (this.syncing) throw new Error('A Meta sync is already running')
    if (candidates.length === 0) return 0
    this.syncing = true
    try {
      const token = await metaAccountService.getToken()
      if (!token) throw new Error('Sign in to your Meta account first')

      let queued = 0
      for (const candidate of candidates) {
        const stageDir = join(
          settingsService.getDownloadPath(),
          'uploads',
          'meta',
          candidate.packageName,
          String(candidate.versionCode)
        )
        try {
          const apkPath = join(stageDir, `${candidate.packageName}.apk`)
          if (!(await this.pathExists(join(stageDir, '.complete')))) {
            await fs.mkdir(stageDir, { recursive: true })
            const emit = (stage: string, percent: number): void =>
              emitProgress({
                packageName: candidate.packageName,
                displayName: candidate.displayName,
                stage,
                percent
              })

            const details = await graphRequest<BinaryDetailsResponse>(
              DOC_BINARY_DETAILS,
              { binaryID: candidate.binaryId },
              token
            )
            const node = details.data?.node
            if (!node) {
              throw new Error('Meta returned no binary details for this title')
            }
            const obbs = [
              ...(node.obb_binary ? [node.obb_binary] : []),
              ...(node.asset_files?.nodes ?? [])
            ].map((file, index) => ({ ...file, name: file.file_name || `data${index}.obb` }))

            emit('Downloading APK', 0)
            await this.downloadBinary(candidate.binaryId, apkPath, token)
            emit('Downloading data files', 10)
            let done = 0
            for (const obb of obbs) {
              if (!obb.name.endsWith(`.${candidate.packageName}.obb`)) {
                throw new Error(
                  `Meta data file "${obb.name}" is not a ${candidate.packageName} OBB`
                )
              }
              await this.downloadBinary(obb.id, join(stageDir, obb.name), token)
              done++
              emit('Downloading data files', 10 + Math.round((done / obbs.length) * 80))
            }
            await fs.writeFile(join(stageDir, '.complete'), new Date().toISOString(), 'utf-8')
          }

          uploadService.addMetaItemToQueue({
            packageName: candidate.packageName,
            gameName: candidate.displayName,
            versionCode: candidate.versionCode,
            sourcePath: apkPath,
            sourceObbPaths: (await fs.readdir(stageDir))
              .filter((name) => name.endsWith(`.${candidate.packageName}.obb`))
              .sort()
              .map((name) => join(stageDir, name))
          })
          queued++
          emitProgress({
            packageName: candidate.packageName,
            displayName: candidate.displayName,
            stage: 'Queued for upload',
            percent: 100
          })
        } catch (error) {
          console.error(`[MetaSync] Failed to fetch ${candidate.displayName}:`, error)
          emitProgress({
            packageName: candidate.packageName,
            displayName: candidate.displayName,
            stage: 'Error',
            percent: 0,
            error: error instanceof Error ? error.message : String(error)
          })
        }
      }
      return queued
    } finally {
      this.syncing = false
    }
  }

  isSyncing(): boolean {
    return this.syncing
  }
}

export default new MetaSyncService()
