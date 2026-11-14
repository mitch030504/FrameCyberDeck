import { getApiKey } from './apiKey'

/**
 * Central scrubbing for text that can reach logs, the UI, or a public paste.
 * It complements (and does not replace) never logging raw child-process errors.
 */
export function redactSecrets(input: unknown): string {
  let text: string
  if (typeof input === 'string') text = input
  else if (input instanceof Error) text = input.message
  else if (input && typeof input === 'object') {
    try {
      text = JSON.stringify(input)
    } catch {
      text = String(input)
    }
  } else text = String(input)

  const key = getApiKey()
  if (key) text = text.split(key).join('[REDACTED]')

  return (
    text
      // 7-Zip passwords, e.g. `-pSECRET` or `-p "SECRET"`.
      .replace(/(\s-p)(?:"[^"]*"|'[^']*'|\S+)/g, '$1[REDACTED]')
      // Tokens in query strings.
      .replace(/([?&](?:access_token|token|api_key|apikey)=)[^&\s]+/gi, '$1[REDACTED]')
      // Headers.
      .replace(/((?:x-api-key|authorization)\s*[:=]\s*)[^\s"',}]+/gi, '$1[REDACTED]')
      // URL userinfo.
      .replace(/(https?:\/\/[^\s/@:]+):([^\s/@]+)@/gi, '$1:[REDACTED]@')
      // Credential-looking JSON fields.
      .replace(
        /(["']?(?:build_secret|client_secret|password|apiKey|api_key)["']?\s*[:=]\s*["']?)[^"'\s,}]+/gi,
        '$1[REDACTED]'
      )
  )
}
