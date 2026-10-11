/* Smoke test: verifies the local-file protocol serves files with webSecurity on.
 * Run: npx electron tests/protocol_smoke.cjs
 * Mirrors the scheme registration and handler in src/main/index.ts. */
const { app, protocol, net, BrowserWindow } = require('electron')
const { pathToFileURL } = require('node:url')
const fs = require('node:fs')

// 1x1 red pixel PNG
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64'
)

// Not registered as "standard": standard schemes parse the first path
// component as a hostname, which mangles absolute filesystem paths.
protocol.registerSchemesAsPrivileged([
  { scheme: 'local-file', privileges: { secure: true, supportFetchAPI: true } }
])

let result = 'FAIL'
app.whenReady().then(async () => {
  try {
    const imgPath = '/tmp/fcd-protocol-smoke.png'
    fs.writeFileSync(imgPath, PNG)

    protocol.handle('local-file', (request) => {
      let raw = request.url.split('#')[0].slice('local-file://'.length)
      raw = decodeURIComponent(raw)
      const pathname = raw.startsWith('/') ? raw : `/${raw}`
      const filePath =
        process.platform === 'win32' && pathname.startsWith('/') ? pathname.slice(1) : pathname
      return net.fetch(pathToFileURL(filePath).toString())
    })

    // main-process fetch through the handler
    const resp = await net.fetch(`local-file://${imgPath}`)
    const buf = Buffer.from(await resp.arrayBuffer())
    const fetchOk = resp.status === 200 && buf.equals(PNG)
    console.log('main fetch ok:', fetchOk)

    // renderer <img> load with default webSecurity (no webPreferences override)
    const win = new BrowserWindow({ show: false })
    await win.loadURL('data:text/html,<img id="i">')
    const imgOk = await win.webContents.executeJavaScript(
      `new Promise((resolve) => {
        const img = document.getElementById('i');
        img.onload = () => resolve(img.naturalWidth === 1 && img.naturalHeight === 1);
        img.onerror = () => resolve(false);
        img.src = 'local-file://${imgPath}';
      })`
    )
    console.log('renderer img ok:', imgOk)

    result = fetchOk && imgOk ? 'PASS' : 'FAIL'
  } catch (err) {
    console.error('smoke test error:', err)
    result = 'FAIL'
  } finally {
    console.log(`RESULT: ${result}`)
    app.exit(result === 'PASS' ? 0 : 1)
  }
})
