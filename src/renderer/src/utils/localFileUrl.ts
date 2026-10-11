/**
 * Local file access goes through the `local-file` protocol (registered in
 * src/main/index.ts) so the renderer can keep webSecurity enabled.
 */
export function localFileUrl(path: string): string {
  const normalized = path.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/')
  return normalized.startsWith('/') ? `local-file://${normalized}` : `local-file:///${normalized}`
}
