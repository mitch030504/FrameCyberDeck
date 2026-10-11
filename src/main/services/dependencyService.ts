import { app } from 'electron'
import { join } from 'path'
import { execSync } from 'child_process'
import { createHash } from 'crypto'
import { promises as fsPromises, existsSync, createWriteStream, chmodSync, copyFileSync } from 'fs'
import axios, { AxiosProgressEvent } from 'axios'
import SevenZip from 'node-7z'
import { ServiceStatus } from '@shared/types'
import { awaitSevenZipStream } from './sevenZipUtils'

// Pinned rclone version. See getRcloneDownloadUrl for the rationale.
const RCLONE_PINNED_VERSION = 'v1.72.1'

// Pinned Android platform-tools release; URL and SHA-256 recorded at pin time
// (Google publishes only SHA-1 in its repository index). Bump both together.
const PLATFORM_TOOLS_PIN: Partial<Record<NodeJS.Platform, { url: string; sha256: string }>> = {
  win32: {
    url: 'https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip',
    sha256: '45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99'
  },
  darwin: {
    url: 'https://dl.google.com/android/repository/platform-tools_r37.0.1-darwin.zip',
    sha256: 'ee39ad5967e95c2a07f04dbcbde96b1a0c916ba376096db5d2f498b7727a5d1d'
  },
  linux: {
    url: 'https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip',
    sha256: 'd230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1'
  }
}

type ProgressCallback = (
  status: DependencyStatus,
  progress: { name: string; percentage: number }
) => void

interface DependencyInfo {
  ready: boolean
  path: string | null
  error: string | null
}

interface RcloneDependencyInfo extends DependencyInfo {
  downloading: boolean
}

interface AdbDependencyInfo extends DependencyInfo {
  downloading: boolean
}

export interface DependencyStatus {
  sevenZip: DependencyInfo
  rclone: RcloneDependencyInfo
  adb: AdbDependencyInfo
  services: ServiceStatus
}

interface GitHubAsset {
  name: string
  browser_download_url: string
}

class DependencyService {
  private binDir: string // Directory within userData for downloaded binaries (like rclone)
  private resourcesBinDir: string // Directory within app resources for bundled binaries (like 7zip)
  private status: DependencyStatus
  private serviceStatus: ServiceStatus

  constructor() {
    this.binDir = join(app.getPath('userData'), 'bin')
    this.resourcesBinDir = app.isPackaged
      ? join(process.resourcesPath, 'bin')
      : join(app.getAppPath(), 'resources', 'bin')

    this.status = {
      sevenZip: { ready: false, path: null, error: null },
      rclone: { ready: false, path: null, error: null, downloading: false },
      adb: { ready: false, path: null, error: null, downloading: false },
      services: 'NOT_INITIALIZED'
    }
    this.serviceStatus = 'NOT_INITIALIZED'
  }

  async initialize(progressCallback?: ProgressCallback, force?: boolean): Promise<ServiceStatus> {
    if (this.serviceStatus === 'INITIALIZING') {
      console.log('DependencyService already initializing, skipping.')
      return 'INITIALIZING'
    }
    if (!force && this.serviceStatus === 'INITIALIZED') {
      console.log('DependencyService already initialized, skipping.')
      return 'INITIALIZED'
    }
    this.serviceStatus = 'INITIALIZING'
    console.log('Initializing DependencyService...')
    try {
      // Ensure userData bin directory exists for downloads like rclone
      await fsPromises.mkdir(this.binDir, { recursive: true })

      this.checkBundled7zip()

      await this.checkOrDownloadRclone(progressCallback)

      await this.checkOrDownloadAdb(progressCallback)

      console.log('DependencyService initialization finished.')
      this.serviceStatus = 'INITIALIZED'

      // Check if all dependencies are ready after initialization
      if (!this.status.sevenZip.ready || !this.status.rclone.ready || !this.status.adb.ready) {
        // Throw an error or handle the situation where dependencies aren't ready
        const missing: ('7zip' | 'rclone' | 'adb')[] = []
        if (!this.status.sevenZip.ready) missing.push('7zip')
        if (!this.status.rclone.ready) missing.push('rclone')
        if (!this.status.adb.ready) missing.push('adb')
        // Construct error message based on what failed
        let errorMessage = `Dependency setup failed. Missing or failed: ${missing.join(', ')}. `
        if (this.status.sevenZip.error) errorMessage += `7zip Error: ${this.status.sevenZip.error} `
        if (this.status.rclone.error) errorMessage += `Rclone Error: ${this.status.rclone.error}`
        if (this.status.adb.error) errorMessage += `ADB Error: ${this.status.adb.error}`

        console.error(errorMessage)
        this.serviceStatus = 'ERROR'
        throw new Error(errorMessage) // Propagate error to caller
      }
    } catch (error) {
      this.serviceStatus = 'ERROR'
      throw error
    }
    return this.serviceStatus
  }

  // --- 7zip ---

  // Updated to point to bundled location
  public get7zPath(): string {
    const platform = process.platform
    let platformDir: string
    let binaryName: string

    switch (platform) {
      case 'win32':
        platformDir = 'win'
        binaryName = '7za.exe'
        break
      case 'linux':
        platformDir = process.arch === 'arm64' ? 'linux-arm64' : 'linux'
        binaryName = '7zzs'
        break
      case 'darwin':
        platformDir = 'mac'
        binaryName = '7zz'
        break
      default:
        console.error(`Unsupported platform for bundled 7zip: ${platform}`)
        throw new Error(`Unsupported platform for bundled 7zip: ${platform}`)
    }

    const fullPath = join(this.resourcesBinDir, app.isPackaged ? '' : platformDir, binaryName)
    console.log(`Calculated 7zip path for ${platform}: ${fullPath}`)
    return fullPath
  }

  public getAaptPath(): string {
    return join(this.resourcesBinDir, 'aapt-arm')
  }

  // New method to check for bundled 7zip
  private checkBundled7zip(): void {
    const source7zPath = this.get7zPath() // Path in resources
    this.status.sevenZip.error = null // Clear previous errors

    if (!existsSync(source7zPath)) {
      this.status.sevenZip.ready = false
      this.status.sevenZip.error = `Bundled 7zip NOT found at source path: ${source7zPath}. Check app packaging.`
      console.error(this.status.sevenZip.error)
      throw new Error(this.status.sevenZip.error)
    }

    console.log(`Found bundled 7zip at source: ${source7zPath}`)

    // Determine the name of the 7zip binary (e.g., '7zzs', '7za.exe')
    const binaryName = source7zPath.split(/[/\\]/).pop() // POSIX and Windows path separators
    if (!binaryName) {
      this.status.sevenZip.ready = false
      this.status.sevenZip.error = `Could not determine binary name from path: ${source7zPath}`
      console.error(this.status.sevenZip.error)
      throw new Error(this.status.sevenZip.error)
    }

    // Target path in the writable binDir (userData/bin)
    const target7zPath = join(this.binDir, binaryName)
    this.status.sevenZip.path = target7zPath // Update status to point to the new path

    // Always overwrite from bundled resources so upgrades get the correct binary.
    // Skipping this copy when the file exists caused dirty-upgrade installs to
    // retain the old 7za.exe from a previous version.
    console.log(`Copying 7zip from ${source7zPath} to ${target7zPath}`)
    copyFileSync(source7zPath, target7zPath)
    console.log(`Successfully copied 7zip to ${target7zPath}`)

    this.status.sevenZip.ready = true // Mark as ready (either copied or already existed)

    // Ensure executable permissions on non-windows for the file at target7zPath
    if (process.platform !== 'win32') {
      try {
        chmodSync(target7zPath, 0o755)
        console.log(`Ensured execute permissions for ${target7zPath}`)
      } catch (chmodError) {
        console.warn(`Failed to ensure execute permissions for ${target7zPath}:`, chmodError)
        this.status.sevenZip.ready = false // Mark as not ready if permissions fail
        this.status.sevenZip.error = `Permission error on copied file: ${chmodError instanceof Error ? chmodError.message : String(chmodError)}`
        throw new Error(this.status.sevenZip.error)
      }
    }
  }

  /**
   * Extract an archive with the bundled 7-Zip.
   *
   * node-7z (v3) treats *any* byte written to 7-Zip's stderr as a fatal error
   * and rejects with a generic `Error('unknown error')` — even benign warnings
   * emitted after the files have already been extracted. On macOS (Apple
   * Silicon in particular) the bundled `7zz` writes such non-fatal output near
   * the end of extraction, which made rclone/adb setup fail despite the archive
   * being fully unpacked.
   *
   * So we don't treat 7-Zip's stderr as the pass/fail signal here. We always
   * wait for the process to finish, capture any error (including the real
   * `stderr` text, which node-7z hides), and let the caller decide success by
   * checking whether the expected files actually landed on disk. The captured
   * error is returned for logging / to enrich a "not found" failure message.
   */
  private async extractWithSevenZip(
    archivePath: string,
    destDir: string,
    progressName: string,
    progressCallback?: ProgressCallback,
    timeoutMs: number = 120_000
  ): Promise<Error | null> {
    const sevenZipPath = this.get7zPath()
    console.log(`Using bundled 7zip at ${sevenZipPath} for extraction.`)
    const stream = SevenZip.extractFull(archivePath, destDir, {
      $bin: sevenZipPath,
      $progress: true
    })
    // Tolerate benign 7-Zip stderr (e.g. dylibs injected by macOS tweak
    // frameworks); a real ERROR still rejects and success is confirmed by the
    // caller's file existence check below.
    const extractionPromise = awaitSevenZipStream(stream, (percent) =>
      progressCallback?.(this.status, { name: progressName, percentage: percent })
    )

    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        try {
          stream.destroy()
        } catch {
          /* stream may already be closed */
        }
        reject(
          new Error(
            `Extraction timed out after ${Math.round(timeoutMs / 1000)}s. ` +
              `This usually means the 7-Zip process could not start or the archive is corrupt. ` +
              `Antivirus software blocking executables in AppData is a common cause on Windows.`
          )
        )
      }, timeoutMs)
    })

    try {
      const captured = await Promise.race([extractionPromise, timeoutPromise])
      console.log(`Archive extracted to ${destDir}`)
      return captured
    } finally {
      clearTimeout(timeoutId)
    }
  }

  // --- rclone ---

  public getRclonePath(): string {
    const platform = process.platform
    const exeSuffix = platform === 'win32' ? '.exe' : ''
    // rclone is downloaded to userData/bin
    return join(this.binDir, `rclone${exeSuffix}`)
  }

  /** Reads the numeric version string from `rclone version`, e.g. "1.72.1". */
  private readRcloneVersion(rclonePath: string): string | null {
    try {
      const output = execSync(`"${rclonePath}" version`, { timeout: 5000, encoding: 'utf-8' })
      // First line looks like: "rclone v1.72.1"
      const match = output.match(/rclone\s+v(\d+\.\d+\.\d+(?:[-.]\S+)?)/i)
      return match ? match[1] : null
    } catch (err) {
      console.warn(`Failed to read rclone version:`, err)
      return null
    }
  }

  private async checkOrDownloadRclone(progressCallback?: ProgressCallback): Promise<void> {
    const expectedPath = this.getRclonePath()
    this.status.rclone.path = expectedPath

    if (existsSync(expectedPath)) {
      // Verify it's the pinned version. Older installs may have grabbed
      // whatever was `latest` at the time, and newer rclone breaks our
      // HTTP-backend usage. Replace it if it doesn't match.
      const installedVersion = this.readRcloneVersion(expectedPath)
      const pinnedNumeric = RCLONE_PINNED_VERSION.replace(/^v/, '')
      if (installedVersion === pinnedNumeric) {
        console.log(`rclone found at ${expectedPath} (v${installedVersion}, matches pin)`)
        this.status.rclone.ready = true
        this.status.rclone.downloading = false
        this.status.rclone.error = null
        return
      }
      console.log(
        `rclone at ${expectedPath} is v${installedVersion ?? 'unknown'}, ` +
          `replacing with pinned ${RCLONE_PINNED_VERSION}`
      )
      try {
        await fsPromises.unlink(expectedPath)
      } catch (err) {
        console.warn(`Failed to remove stale rclone binary, will overwrite:`, err)
      }
    }

    console.log(`rclone not found at ${expectedPath}, attempting download.`)
    this.status.rclone.ready = false
    this.status.rclone.downloading = true
    this.status.rclone.error = null
    progressCallback?.(this.status, { name: 'rclone', percentage: 0 })

    let tempArchivePath: string | null = null
    let tempExtractDir: string | null = null

    try {
      const download = await this.getRcloneDownloadUrl()
      if (!download) {
        throw new Error('Could not find suitable rclone download URL.')
      }

      tempArchivePath = join(app.getPath('temp'), `rclone-download-${Date.now()}.zip`)
      console.log(`Downloading rclone from ${download.url} to ${tempArchivePath}`)

      const response = await axios.get(download.url, {
        responseType: 'stream',
        onDownloadProgress: (progressEvent: AxiosProgressEvent) => {
          if (progressEvent.total) {
            const percentage = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            progressCallback?.(this.status, { name: 'rclone', percentage })
          }
        }
      })

      const writer = createWriteStream(tempArchivePath)
      response.data.pipe(writer)
      await new Promise<void>((resolve, reject) => {
        writer.on('finish', resolve)
        writer.on('error', reject)
        response.data.on('error', reject)
      })

      console.log(`rclone download complete: ${tempArchivePath}`)
      await this.verifySha256(tempArchivePath, download.sha256, 'rclone')
      progressCallback?.(this.status, { name: 'rclone', percentage: 100 })

      // --- Extraction Step ---
      tempExtractDir = join(app.getPath('temp'), `rclone-extract-${Date.now()}`)
      console.log(`Extracting archive: ${tempArchivePath} to ${tempExtractDir}`)
      progressCallback?.(this.status, { name: 'rclone-extract', percentage: 0 })

      // Use the bundled 7zip for extraction if it's ready
      if (!this.status.sevenZip.ready) {
        throw new Error('Bundled 7zip is not available or ready, cannot extract rclone archive.')
      }

      const extractionError = await this.extractWithSevenZip(
        tempArchivePath!,
        tempExtractDir!,
        'rclone-extract',
        progressCallback
      )

      // Find the binary within the extracted files (usually in a subdirectory)
      const binaryName = process.platform === 'win32' ? 'rclone.exe' : 'rclone'
      let foundBinaryPath: string | null = null

      // Rclone zip extracts into a folder like rclone-vX.Y.Z-os-arch/
      const entries = await fsPromises.readdir(tempExtractDir, { withFileTypes: true })
      for (const entry of entries) {
        if (entry.isDirectory()) {
          const potentialPath = join(tempExtractDir, entry.name, binaryName)
          if (existsSync(potentialPath)) {
            foundBinaryPath = potentialPath
            break
          }
        }
        // Check root of extracted dir as well (less common for rclone zip)
        const rootPath = join(tempExtractDir, binaryName)
        if (!foundBinaryPath && existsSync(rootPath)) {
          foundBinaryPath = rootPath
        }
      }

      if (!foundBinaryPath) {
        // Extraction genuinely failed — surface the real 7-Zip stderr if we have it.
        const detail = extractionError
          ? ` 7zip error: ${(extractionError as Error & { stderr?: string }).stderr?.trim() || extractionError.message}`
          : ''
        console.error(`Could not find ${binaryName} within extracted files in ${tempExtractDir}`)
        throw new Error(`Could not locate ${binaryName} after extraction.${detail}`)
      }

      console.log(`Found rclone binary at ${foundBinaryPath}. Copying to ${expectedPath}...`)
      await fsPromises.copyFile(foundBinaryPath, expectedPath)
      console.log(`Successfully copied rclone binary to ${expectedPath}`)

      // Clean up temp dirs and archive
      console.log(`Cleaning up temporary files: ${tempExtractDir} and ${tempArchivePath}`)
      await fsPromises.rm(tempExtractDir, { recursive: true, force: true })
      await fsPromises.unlink(tempArchivePath)
      tempArchivePath = null

      this.status.rclone.ready = true
      this.status.rclone.error = null
      progressCallback?.(this.status, { name: 'rclone-extract', percentage: 100 })

      // Set executable permissions
      if (process.platform !== 'win32') {
        try {
          await fsPromises.chmod(expectedPath, 0o755)
          console.log(`Set executable permissions for ${expectedPath}`)
        } catch (chmodError) {
          console.warn(`Failed to set executable permissions for ${expectedPath}:`, chmodError)
          // Consider marking rclone as not ready if chmod fails
          this.status.rclone.ready = false
          this.status.rclone.error = `Permission error: ${chmodError instanceof Error ? chmodError.message : String(chmodError)}`
        }
      }
    } catch (error) {
      console.error(
        'Error during rclone download/extraction:',
        error instanceof Error ? error.message : String(error)
      )
      this.status.rclone.ready = false
      this.status.rclone.downloading = false
      this.status.rclone.error = error instanceof Error ? error.message : 'Unknown download error'
      try {
        if (tempArchivePath && existsSync(tempArchivePath)) {
          await fsPromises.unlink(tempArchivePath)
          console.log(`Cleaned up rclone temp archive: ${tempArchivePath}`)
        }
        if (tempExtractDir && existsSync(tempExtractDir)) {
          await fsPromises.rm(tempExtractDir, { recursive: true, force: true })
          console.log(`Cleaned up rclone temp extraction directory: ${tempExtractDir}`)
        }
      } catch (cleanupError) {
        console.error('Failed to cleanup rclone temp files on error:', cleanupError)
      }
    } finally {
      this.status.rclone.downloading = false
    }
  }

  /** Verify a downloaded archive against an expected SHA-256 before it is run. */
  private async verifySha256(filePath: string, expected: string, label: string): Promise<void> {
    const data = await fsPromises.readFile(filePath)
    const actual = createHash('sha256').update(data).digest('hex')
    if (actual !== expected) {
      throw new Error(
        `${label} SHA-256 mismatch: expected ${expected}, got ${actual}. ` +
          'The download may be corrupt or tampered with; refusing to use it.'
      )
    }
    console.log(`${label} SHA-256 verified: ${actual}`)
  }

  private async getRcloneDownloadUrl(): Promise<{ url: string; sha256: string } | null> {
    const repo = 'rclone/rclone'
    // Pinned to match the version Rookie sideloader ships against the same
    // public VRP endpoint. Newer rclone releases tightened HTTP backend URL
    // handling and `sync` argument validation in ways that broke us, while
    // 1.72.1 is known-working. Bump deliberately, not automatically.
    const apiUrl = `https://api.github.com/repos/${repo}/releases/tags/${RCLONE_PINNED_VERSION}`
    console.log(`Fetching pinned rclone ${RCLONE_PINNED_VERSION} release info from ${apiUrl}`)

    try {
      const response = await axios.get(apiUrl, { timeout: 15000 })
      const release = response.data
      const assets: GitHubAsset[] = release?.assets

      if (!assets || !Array.isArray(assets)) {
        console.error('No rclone assets found in the latest release data.')
        return null
      }

      console.log(`Found ${assets.length} rclone assets for release ${release.tag_name}`)

      const platform = process.platform
      const arch = process.arch
      let platformSuffix = ''
      let archSuffix = ''

      // Determine platform suffix
      if (platform === 'win32') platformSuffix = 'windows'
      else if (platform === 'darwin') platformSuffix = 'osx'
      else if (platform === 'linux') platformSuffix = 'linux'
      else return null // Unsupported platform

      // Determine arch suffix
      if (arch === 'x64') archSuffix = 'amd64'
      else if (arch === 'arm64') archSuffix = 'arm64'
      else if (arch === 'ia32')
        archSuffix = '386' // rclone uses 386 for ia32
      // Add arm? rclone might use 'arm'
      else return null // Unsupported arch

      // Rclone uses .zip for all these combos
      const targetFileNamePattern = `-${platformSuffix}-${archSuffix}.zip`
      console.log(`Searching for rclone asset ending with: ${targetFileNamePattern}`)

      const targetAsset = assets.find((a) => a.name.endsWith(targetFileNamePattern))

      if (!targetAsset?.browser_download_url) {
        console.error(`Could not find a suitable rclone asset for pattern ${targetFileNamePattern}`)
        return null
      }

      // Verify the archive against the release's own SHA256SUMS manifest;
      // never run an unverified download.
      const sumsAsset = assets.find((a) => a.name === 'SHA256SUMS')
      if (!sumsAsset?.browser_download_url) {
        console.error('rclone release has no SHA256SUMS asset; refusing unverified download.')
        return null
      }
      const sumsResponse = await axios.get(sumsAsset.browser_download_url, { timeout: 15000 })
      const sumsLine = String(sumsResponse.data)
        .split('\n')
        .find((line) => line.trim().endsWith(targetAsset.name))
      const expectedSha = sumsLine?.trim().split(/\s+/)[0]
      if (!expectedSha || !/^[0-9a-f]{64}$/i.test(expectedSha)) {
        console.error(`SHA256SUMS has no entry for ${targetAsset.name}; refusing download.`)
        return null
      }

      console.log(`Selected rclone asset: ${targetAsset.name}`)
      return { url: targetAsset.browser_download_url, sha256: expectedSha.toLowerCase() }
    } catch (error) {
      console.error(
        `Error fetching rclone release info from GitHub:`,
        error instanceof Error ? error.message : String(error)
      )
      return null
    }
  }

  // --- ADB ---

  public getAdbPath(): string {
    const platform = process.platform
    const exeSuffix = platform === 'win32' ? '.exe' : ''
    // adb is downloaded to userData/bin
    return join(this.binDir, `adb${exeSuffix}`)
  }

  private async checkOrDownloadAdb(progressCallback?: ProgressCallback): Promise<void> {
    const expectedPath = this.getAdbPath()
    this.status.adb.path = expectedPath

    if (existsSync(expectedPath)) {
      console.log(`adb found at ${expectedPath}`)
      this.status.adb.ready = true
      this.status.adb.downloading = false
      this.status.adb.error = null
      // Ensure executable permissions on non-windows (even if it exists)
      if (process.platform !== 'win32') {
        try {
          await fsPromises.chmod(expectedPath, 0o755)
        } catch (chmodError) {
          console.warn(
            `Failed to ensure execute permissions for existing adb ${expectedPath}:`,
            chmodError
          )
          this.status.adb.ready = false // Mark as not ready if permissions fail
          this.status.adb.error = `Permission error: ${chmodError instanceof Error ? chmodError.message : String(chmodError)}`
        }
      }
      return
    }

    console.log(`adb not found at ${expectedPath}, attempting download.`)
    this.status.adb.ready = false
    this.status.adb.downloading = true
    this.status.adb.error = null
    progressCallback?.(this.status, { name: 'adb', percentage: 0 })

    // ARM64 Linux: Google doesn't ship ARM64 platform-tools, use system adb
    if (process.platform === 'linux' && process.arch === 'arm64') {
      let systemAdb: string | null = null
      try {
        const result = execSync('which adb', { encoding: 'utf-8' }).trim()
        if (result && existsSync(result)) systemAdb = result
      } catch {
        /* not in PATH */
      }
      if (!systemAdb) {
        for (const candidate of ['/usr/bin/adb', '/usr/local/bin/adb', '/bin/adb']) {
          if (existsSync(candidate)) {
            systemAdb = candidate
            break
          }
        }
      }
      if (!systemAdb) {
        this.status.adb.downloading = false
        this.status.adb.error = 'ARM64 Linux: adb not found. Install it first: sudo apt install adb'
        throw new Error(this.status.adb.error)
      }
      console.log(`[ARM64 Linux] Using system adb at ${systemAdb}`)
      await fsPromises.copyFile(systemAdb, expectedPath)
      await fsPromises.chmod(expectedPath, 0o755)
      this.status.adb.ready = true
      this.status.adb.error = null
      this.status.adb.downloading = false
      progressCallback?.(this.status, { name: 'adb', percentage: 100 })
      return
    }

    let tempArchivePath: string | null = null
    let tempExtractDir: string | null = null

    try {
      const platform = process.platform
      const pin = PLATFORM_TOOLS_PIN[platform]
      if (!pin) {
        throw new Error(`Unsupported platform for adb download: ${platform}`)
      }
      const downloadUrl = pin.url

      tempArchivePath = join(app.getPath('temp'), `platform-tools-download-${Date.now()}.zip`)
      console.log(`Downloading adb platform-tools from ${downloadUrl} to ${tempArchivePath}`)

      const response = await axios.get(downloadUrl, {
        responseType: 'stream',
        timeout: 30000, // Increased timeout for potentially larger download
        onDownloadProgress: (progressEvent: AxiosProgressEvent) => {
          if (progressEvent.total) {
            const percentage = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            progressCallback?.(this.status, { name: 'adb', percentage })
          }
        }
      })

      const writer = createWriteStream(tempArchivePath)
      response.data.pipe(writer)
      await new Promise<void>((resolve, reject) => {
        writer.on('finish', resolve)
        writer.on('error', reject)
        response.data.on('error', reject) // Propagate errors from the download stream
      })

      console.log(`adb platform-tools download complete: ${tempArchivePath}`)
      progressCallback?.(this.status, { name: 'adb', percentage: 100 })

      // Validate the downloaded archive before trying to extract it. A
      // corrupt, truncated, or intercepted download (e.g. a captive-portal
      // HTML page returned with HTTP 200) would make 7-Zip hang or fail with
      // a misleading error. Platform-tools zips are always >5 MB.
      const downloadedStat = await fsPromises.stat(tempArchivePath)
      if (downloadedStat.size < 1024 * 1024) {
        throw new Error(
          `Downloaded ADB archive is too small (${downloadedStat.size} bytes) — ` +
            `the file is likely corrupt or your network returned an error page. ` +
            `Check your internet connection and try again.`
        )
      }
      console.log(`Downloaded archive size: ${downloadedStat.size} bytes`)
      await this.verifySha256(tempArchivePath!, pin.sha256, 'adb platform-tools')

      // --- Extraction Step using SevenZip ---
      tempExtractDir = join(app.getPath('temp'), `adb-extract-${Date.now()}`)
      console.log(`Extracting adb from archive: ${tempArchivePath} to ${tempExtractDir}`)
      progressCallback?.(this.status, { name: 'adb-extract', percentage: 0 })

      // Use the bundled 7zip for extraction if it's ready
      if (!this.status.sevenZip.ready) {
        throw new Error('Bundled 7zip is not available or ready, cannot extract adb archive.')
      }

      const extractionError = await this.extractWithSevenZip(
        tempArchivePath!,
        tempExtractDir!,
        'adb-extract',
        progressCallback
      )

      // Copy required files from extracted platform-tools directory to binDir
      const isWindows = platform === 'win32'
      const adbBinaryName = isWindows ? 'adb.exe' : 'adb'
      const requiredFilesBaseNames = isWindows
        ? [adbBinaryName, 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'libwinpthread-1.dll']
        : [adbBinaryName]

      const platformToolsDir = join(tempExtractDir, 'platform-tools')

      if (!existsSync(platformToolsDir)) {
        // Extraction genuinely failed — surface the real 7-Zip stderr if we have it.
        const detail = extractionError
          ? ` 7zip error: ${(extractionError as Error & { stderr?: string }).stderr?.trim() || extractionError.message}`
          : ''
        throw new Error(
          `Platform-tools directory not found in extracted archive: ${platformToolsDir}.${detail}`
        )
      }

      console.log(`Copying required ADB files from ${platformToolsDir} to ${this.binDir}`)

      for (const fileName of requiredFilesBaseNames) {
        const sourcePath = join(platformToolsDir, fileName)
        const targetPath = join(this.binDir, fileName)

        if (!existsSync(sourcePath)) {
          throw new Error(`Required file not found in platform-tools: ${fileName}`)
        }

        await fsPromises.copyFile(sourcePath, targetPath)
        console.log(`Copied ${fileName} to ${targetPath}`)
      }

      // Clean up temp extraction directory
      console.log(`Cleaning up temporary extraction directory: ${tempExtractDir}`)
      await fsPromises.rm(tempExtractDir, { recursive: true, force: true })
      tempExtractDir = null

      // Clean up temp archive
      console.log(`Cleaning up temporary archive: ${tempArchivePath}`)
      await fsPromises.unlink(tempArchivePath)
      tempArchivePath = null

      this.status.adb.ready = true
      this.status.adb.error = null
      progressCallback?.(this.status, { name: 'adb-extract', percentage: 100 })

      // Set executable permissions ONLY for the main adb binary
      if (process.platform !== 'win32') {
        try {
          // expectedPath still points to the main adb binary path
          await fsPromises.chmod(expectedPath, 0o755)
          console.log(`Set executable permissions for ${expectedPath}`)
        } catch (chmodError) {
          console.warn(`Failed to set executable permissions for ${expectedPath}:`, chmodError)
          this.status.adb.ready = false // Mark as not ready if permissions fail
          this.status.adb.error = `Permission error: ${chmodError instanceof Error ? chmodError.message : String(chmodError)}`
        }
      }
    } catch (error) {
      console.error(
        'Error during adb download/extraction:',
        error instanceof Error ? error.message : String(error)
      )
      console.error(error)
      this.status.adb.ready = false
      this.status.adb.downloading = false
      this.status.adb.error =
        error instanceof Error ? error.message : 'Unknown download/extraction error'
      // Clean up temp files on error if they exist
      try {
        if (tempArchivePath && existsSync(tempArchivePath)) {
          await fsPromises.unlink(tempArchivePath)
          console.log(`Cleaned up adb temp archive on error: ${tempArchivePath}`)
        }
        if (tempExtractDir && existsSync(tempExtractDir)) {
          await fsPromises.rm(tempExtractDir, { recursive: true, force: true })
          console.log(`Cleaned up adb temp extraction directory on error: ${tempExtractDir}`)
        }
      } catch (cleanupError) {
        console.error('Failed to cleanup adb temp files on error:', cleanupError)
      }
    } finally {
      this.status.adb.downloading = false
    }
  }

  // --- Public Methods ---

  getStatus(): DependencyStatus {
    return { ...this.status } // Return a copy to avoid direct modification
  }

  setDependencyServiceStatus(status: ServiceStatus): void {
    this.status.services = status
  }
}

export default new DependencyService()
