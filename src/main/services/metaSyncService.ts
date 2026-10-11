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
const DOC_BINARY_DETAILS = '4734929166632773'
const DOWNLOAD_URI = 'https://securecdn.oculus.com/binaries/download/'

interface EntitlementsResponse {
  data: {
    viewer?: {
      user?: {
        active_entitlements?: {
          nodes?: {
            grant_reason?: string
            item?: {
              id?: string
              display_name?: string
              canonical_name?: string
              platform?: string
              latest_supported_binary?: {
                id?: string
                version_code?: number
                versionCode?: number
                version?: string
                package_name?: string
              } | null
            }
          }[]
        }
      }
    }
  }
}
interface BinaryDetailsResponse {
  data?: {
    node?: {
      obb_binary?: { id: string; file_name?: string } | null
      asset_files?: { nodes: { id: string; file_name?: string }[] }
    }
  }
}

export interface EntitledTitle {
  appId: string
  displayName: string
  packageName: string
  versionCode: number
  version: string
  binaryId: string
  /** Why the account owns the title (PAID_OFFER, UNKNOWN, NUX, ...). */
  grantReason: string
}

/**
 * Grant reasons that mark a purchase: only these justify adding a title
 * that is not on the server yet. Free downloads (NUX, UNKNOWN, promo keys)
 * stay off the server. Updates to titles the server already carries are
 * always offered regardless of how they were originally obtained.
 * SUBSCRIPTION covers Horizon+ (Meta Quest+) claimed titles.
 */
export const PAID_GRANT_REASONS = new Set(['PAID_OFFER', 'PREORDER', 'XBUY', 'SUBSCRIPTION'])

/** Paid for the purpose of a new upload, by grant reason. */
export function isPaidGrantReason(reason: string): boolean {
  return PAID_GRANT_REASONS.has(reason) || reason.includes('SUBSCRIPTION')
}

/**
 * Pick, from everything the account is entitled to, the titles that are new
 * or newer than what the server catalog already offers. Pure so it can be
 * tested without touching Meta or the network.
 */
export function selectCandidates(
  entitledTitles: EntitledTitle[],
  catalog: { packageName: string; versionCode: number }[],
  isBlacklisted: (packageName: string, versionCode: number) => boolean
): MetaSyncCandidate[] {
  const serverVersionByPackage = new Map(
    catalog.map((entry) => [entry.packageName, entry.versionCode])
  )
  const candidates: MetaSyncCandidate[] = []
  for (const title of entitledTitles) {
    const serverVersion = serverVersionByPackage.get(title.packageName)
    if (serverVersion !== undefined && title.versionCode <= serverVersion) continue
    if (serverVersion === undefined && !isPaidGrantReason(title.grantReason)) continue
    if (isBlacklisted(title.packageName, title.versionCode)) continue
    candidates.push({
      packageName: title.packageName,
      appId: title.appId,
      displayName: title.displayName,
      versionCode: title.versionCode,
      version: title.version,
      binaryId: title.binaryId,
      kind: serverVersion === undefined ? 'new' : 'update',
      serverVersionCode: serverVersion
    })
  }
  return candidates
}

class MetaSyncService {
  private syncing = false

  async findUpdates(): Promise<MetaSyncCandidate[]> {
    if (this.syncing) throw new Error('A Meta sync is already running')
    const token = await metaAccountService.getToken()
    if (!token) throw new Error('Sign in to your Meta account first')

    // One query: the entitlement item carries the latest supported binary
    // (id, version_code, package_name) for Android titles. Per-app version
    // history queries are unnecessary and quickly trip Meta's rate limiter.
    const entitlements = await graphRequest<EntitlementsResponse>(DOC_ENTITLEMENTS, {}, token)
    const nodes = entitlements.data.viewer?.user?.active_entitlements?.nodes
    if (!Array.isArray(nodes)) {
      throw new Error('Meta returned no entitlement list for this account')
    }

    const entitledTitles: EntitledTitle[] = []
    for (const node of nodes) {
      const item = node.item ?? {}
      const latest = item.latest_supported_binary
      const versionCode = latest?.version_code ?? latest?.versionCode
      // PC titles have no package name; DLC/bundle items have no binary here.
      if (!item.platform?.startsWith('ANDROID')) continue
      if (!latest?.package_name || !versionCode || !latest.id) continue
      entitledTitles.push({
        appId: item.id ?? '',
        displayName: item.display_name || item.canonical_name || item.id || '',
        packageName: latest.package_name,
        versionCode,
        version: latest.version ?? String(versionCode),
        binaryId: latest.id,
        grantReason: node.grant_reason ?? 'UNKNOWN'
      })
    }

    const catalog = (await gameService.getGames())
      .map((game) => ({ packageName: game.packageName, versionCode: Number(game.version) || 0 }))
      .filter((entry) => entry.packageName)
    return selectCandidates(entitledTitles, catalog, (packageName, versionCode) =>
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
