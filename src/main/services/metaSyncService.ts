import { promises as fs, createWriteStream } from 'fs'
import { join, basename } from 'path'
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
// Leaner binary query: no asset-file names or DLC flags, but survives the
// binaries the rich query rejects ("reduce the amount of data").
const DOC_BINARY_DETAILS_LEAN = '24072064135771905'
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
interface BinaryAssetFile {
  id: string
  file_name?: string
  /** Byte size as a decimal string, when Meta reports it. */
  size?: string | number
  /** Rich query only: false marks optional content the Quest installer skips. */
  is_required?: boolean | null
  /** Rich query only: set for entitlement-gated store DLC. */
  iap_item?: { id: string } | null
}

interface BinaryDetailsResponse {
  data?: {
    node?: {
      /** Byte size of the APK itself, when Meta reports it. */
      size?: string | number
      obb_binary?: { id: string; file_name?: string; size?: string | number } | null
      asset_files?: { nodes?: BinaryAssetFile[] }
    }
  }
}

type BinaryDetailsNode = NonNullable<NonNullable<BinaryDetailsResponse['data']>['node']>

// Add-ons (iap items) for an app: this doc now only lists the packs and
// whether the viewer is entitled to them — file metadata is no longer part
// of the response. The pack a file belongs to comes from the rich binary
// query's iap_item link, which is what ties owned packs to their files.
const DOC_DLC = '3853229151363174'

interface DlcItem {
  id?: string
  is_viewer_entitled?: boolean
}

interface DlcResponse {
  data?: {
    node?: {
      latest_supported_binary?: {
        firstIapItems?: { edges?: { node?: DlcItem }[]; nodes?: DlcItem[] }
      }
      firstIapItems?: { edges?: { node?: DlcItem }[]; nodes?: DlcItem[] }
      iap_items?: { nodes?: DlcItem[] }
    }
  }
}

/**
 * A release file name Meta can be trusted with: no path traversal, control
 * characters, Windows-reserved device names, or trailing dots/spaces that
 * break later extraction. Returns null when the name cannot be used.
 */
export function safeFileName(raw: string): string | null {
  if (
    !raw ||
    raw.length > 220 ||
    // Control characters are exactly what this check exists to reject.
    // eslint-disable-next-line no-control-regex
    /[<>:"/\\|?*\x00-\x1f]/.test(raw) ||
    /[. ]$/.test(raw) ||
    /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(raw)
  ) {
    return null
  }
  return raw
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
 * Demo detection for new-title candidates: Meta grants some demos through
 * PAID_OFFER, so the grant reason alone does not exclude them. A demo's
 * display name carries the word "demo"; its package's last segment ends with
 * it (com.beatgames.beatsaber.demo, com.survios.CreedDemo).
 */
const DEMO_NAME = /\bdemo\b/i
const DEMO_PACKAGE = /demo$/i

function isDemoTitle(title: EntitledTitle): boolean {
  const lastSegment = title.packageName.split('.').pop() ?? ''
  return DEMO_NAME.test(title.displayName) || DEMO_PACKAGE.test(lastSegment)
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
    if (
      serverVersion === undefined &&
      (isDemoTitle(title) || !isPaidGrantReason(title.grantReason))
    )
      continue
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

  /**
   * Two persisted queries expose a binary's files. The rich one names each
   * asset file and marks DLC, but Meta rejects it ("reduce the amount of
   * data") on binaries with very large asset lists — Beat Saber lists 294
   * DLC packs. The lean one returns at least the OBB for those.
   */
  private async fetchBinaryDetails(
    binaryId: string,
    token: string
  ): Promise<{ node: BinaryDetailsNode; lean: boolean }> {
    const parse = (response: BinaryDetailsResponse): BinaryDetailsNode => {
      const node = response.data?.node
      if (!node) throw new Error('Meta returned no binary details for this title')
      return node
    }
    try {
      return {
        node: parse(
          await graphRequest<BinaryDetailsResponse>(
            DOC_BINARY_DETAILS,
            { binaryID: binaryId },
            token
          )
        ),
        lean: false
      }
    } catch {
      return {
        node: parse(
          await graphRequest<BinaryDetailsResponse>(
            DOC_BINARY_DETAILS_LEAN,
            { binaryID: binaryId },
            token
          )
        ),
        lean: true
      }
    }
  }

  /**
   * Download one Meta file with resume support (adapted from AXRB's
   * download.mjs, GPLv3): bytes land in a `.part` file next to an
   * `{size, etag}` sidecar. A retry continues from the byte offset when the
   * ETag still matches; the part file is only renamed over the destination
   * once its exact size has arrived, so an interrupted download can never be
   * mistaken for a complete one.
   */
  private async downloadBinary(
    id: string,
    destination: string,
    token: string,
    expectedSize = 0,
    onProgress?: (received: number, total: number) => void
  ): Promise<void> {
    const part = `${destination}.part`
    const sidecar = `${part}.json`
    // The token is a query parameter; never log this URL.
    const url = `${DOWNLOAD_URI}?id=${id}&access_token=${encodeURIComponent(token)}`

    let offset = 0
    let resumeEtag = ''
    try {
      const meta = JSON.parse(await fs.readFile(sidecar, 'utf-8')) as {
        size?: number
        etag?: string
      }
      const stat = await fs.stat(part)
      // Only resume against a stable ETag with the expected size on record.
      if (
        expectedSize &&
        meta.size === expectedSize &&
        meta.etag &&
        !meta.etag.startsWith('W/') &&
        stat.size <= expectedSize
      ) {
        offset = stat.size
        resumeEtag = meta.etag
      }
    } catch {
      /* no previous attempt: start from scratch */
    }

    const headers: Record<string, string> = { 'User-Agent': 'OculusGraphQLApiLib/1.0' }
    if (offset) {
      headers.Range = `bytes=${offset}-`
      headers['If-Range'] = resumeEtag
    }
    const response = await fetch(url, { headers })
    if (!response.ok || !response.body) {
      throw new Error(`Download failed (${response.status})`)
    }
    if (response.status === 206) {
      const range = response.headers.get('content-range')?.match(/^bytes (\d+)-(\d+)\/(\d+)$/)
      if (!range || Number(range[1]) !== offset) {
        throw new Error('Server returned an inconsistent download range.')
      }
    } else if (offset) {
      offset = 0 // server ignored the range: restart the file from scratch
    }
    await fs.writeFile(
      sidecar,
      JSON.stringify({ size: expectedSize, etag: response.headers.get('etag') || '' })
    )

    // Abort after 30 minutes without a chunk; a slow transfer is fine, a dead
    // one must not hang the sync forever.
    const idle = new AbortController()
    const timer = setTimeout(
      () => idle.abort(new Error('Download stalled for 30 minutes.')),
      30 * 60 * 1000
    )
    let received = offset
    try {
      await pipeline(
        async function* () {
          for await (const chunk of Readable.fromWeb(response.body as never)) {
            idle.signal.throwIfAborted()
            timer.refresh()
            received += chunk.length
            if (expectedSize && received > expectedSize) {
              throw new Error('Download exceeded its expected size.')
            }
            onProgress?.(received, expectedSize)
            yield chunk
          }
        },
        createWriteStream(part, { flags: offset ? 'a' : 'w' }),
        { signal: idle.signal }
      )
      if (!received || (expectedSize && received !== expectedSize)) {
        throw new Error('Download ended before the complete file arrived.')
      }
    } catch (error) {
      // Keep the part file and sidecar so a retry can resume.
      const reason = idle.signal.aborted ? idle.signal.reason : error
      throw reason instanceof Error ? reason : new Error(String(reason))
    } finally {
      clearTimeout(timer)
    }
    await fs.rename(part, destination)
    await fs.rm(sidecar, { force: true })
  }

  /**
   * A release is downloaded, then copied into upload staging and zipped:
   * require room for roughly two copies before starting a multi-gigabyte
   * fetch, instead of failing halfway through.
   */
  private async assertFreeSpace(directory: string, bytes: number): Promise<void> {
    if (!bytes) return
    const stats = await fs.statfs(directory)
    const free = Number(stats.bavail) * Number(stats.bsize)
    if (free < bytes * 2) {
      throw new Error(
        `Not enough disk space: this release needs about ${(bytes / 1e9).toFixed(1)} GB plus the same again for upload staging.`
      )
    }
  }

  /**
   * Store add-on (DLC) ids this account is entitled to, so paid add-on
   * files (e.g. music packs) reach the server along with the base release.
   * A failed lookup only drops add-ons for that title, never the release.
   */
  private async fetchOwnedDlcItemIds(appId: string, token: string): Promise<Set<string>> {
    const owned = new Set<string>()
    if (!appId) return owned
    try {
      const response = await graphRequest<DlcResponse>(
        DOC_DLC,
        { id: appId, first: 200, last: null, after: null, before: null, forward: true },
        token
      )
      const binary = response.data?.node?.latest_supported_binary ?? response.data?.node
      const items = [
        ...(binary?.firstIapItems?.edges ?? []).map((edge) => edge.node),
        ...(binary?.firstIapItems?.nodes ?? []),
        ...(response.data?.node?.iap_items?.nodes ?? [])
      ]
      for (const item of items) {
        if (item?.id && item.is_viewer_entitled) owned.add(item.id)
      }
    } catch (error) {
      console.warn(`[MetaSync] Could not check owned add-ons for ${appId}:`, error)
    }
    return owned
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

            const { node, lean } = await this.fetchBinaryDetails(candidate.binaryId, token)
            // Required files plus owned add-ons: a store-DLC file needs its
            // pack's entitlement; optional non-DLC content the Quest installer
            // skips stays off the server.
            const ownedDlcIds = lean
              ? new Set<string>() // no asset names on this path, so no add-ons
              : await this.fetchOwnedDlcItemIds(candidate.appId, token)
            const assets = lean
              ? // Lean nodes carry no file names; such binaries (huge DLC
                // lists, e.g. Beat Saber) ship the base game as APK + OBB.
                []
              : (node.asset_files?.nodes ?? []).filter((file) => {
                  const dlcId = file.iap_item?.id
                  return dlcId ? ownedDlcIds.has(dlcId) : file.is_required !== false
                })
            // Releases ship data under their own names (Unity bundles, Unreal
            // .pak chunks, Sounds.obb), not the classic main.<pkg>.obb form.
            const obbs: (BinaryAssetFile & { name: string })[] = []
            const seenNames = new Set<string>()
            for (const [index, file] of [
              ...(node.obb_binary ? [node.obb_binary] : []),
              ...assets
            ].entries()) {
              // An unsafe name falls back to a generic one instead of
              // blocking the release; duplicates are dropped.
              const name =
                safeFileName(basename(file.file_name || `data${index}.obb`)) ?? `data${index}.obb`
              if (seenNames.has(name)) continue
              seenNames.add(name)
              obbs.push({ ...file, name })
            }

            // A release is downloaded, then copied into upload staging and
            // zipped: require room for roughly two copies before starting.
            const totalSize = [
              Number(node.size) || 0,
              ...obbs.map((file) => Number(file.size) || 0)
            ].reduce((sum, size) => sum + size, 0)
            await this.assertFreeSpace(stageDir, totalSize)

            const apkSize = Number(node.size) || 0
            emit('Downloading APK', 0)
            await this.downloadBinary(
              candidate.binaryId,
              apkPath,
              token,
              apkSize,
              (received, total) => {
                emit('Downloading APK', total ? Math.round((received / total) * 10) : 0)
              }
            )
            emit('Downloading data files', 10)
            let done = 0
            for (const obb of obbs) {
              await this.downloadBinary(
                obb.id,
                join(stageDir, obb.name),
                token,
                Number(obb.size) || 0,
                (received, total) => {
                  const fraction = total ? received / total : 0
                  emit(
                    'Downloading data files',
                    10 + Math.round(((done + fraction) / obbs.length) * 80)
                  )
                }
              )
              done++
            }
            await fs.writeFile(join(stageDir, '.complete'), new Date().toISOString(), 'utf-8')
          }

          // Data files are everything in the stage dir except the APK and the
          // hidden completion marker.
          const dataFiles = (await fs.readdir(stageDir)).filter(
            (name) => !name.startsWith('.') && name !== `${candidate.packageName}.apk`
          )
          uploadService.addMetaItemToQueue({
            packageName: candidate.packageName,
            gameName: candidate.displayName,
            versionCode: candidate.versionCode,
            sourcePath: apkPath,
            sourceObbPaths: dataFiles.map((name) => join(stageDir, name)).sort()
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
