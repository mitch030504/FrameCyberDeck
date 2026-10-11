export function isTrustedRendererUrl(candidate: string, expected: string): boolean {
  try {
    const actual = new URL(candidate)
    const trusted = new URL(expected)
    return (
      actual.protocol === trusted.protocol &&
      actual.host === trusted.host &&
      actual.pathname === trusted.pathname &&
      !actual.username &&
      !actual.password
    )
  } catch {
    return false
  }
}

export function isExternalWebUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
  } catch {
    return false
  }
}
