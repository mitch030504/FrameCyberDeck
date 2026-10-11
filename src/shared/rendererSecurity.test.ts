import { expect, it } from 'vitest'
import { isExternalWebUrl, isTrustedRendererUrl } from './rendererSecurity'

it('only trusts the exact renderer page, including its protocol and host', () => {
  const expected = 'file:///app/renderer/index.html'
  expect(isTrustedRendererUrl(`${expected}#games`, expected)).toBe(true)
  for (const url of [
    'file:///tmp/index.html',
    'https://evil.test/app/renderer/index.html',
    'about:blank',
    'invalid'
  ]) {
    expect(isTrustedRendererUrl(url, expected)).toBe(false)
  }
  expect(isTrustedRendererUrl('http://localhost:5173/#games', 'http://localhost:5173/')).toBe(true)
  expect(isTrustedRendererUrl('http://localhost:5173/evil', 'http://localhost:5173/')).toBe(false)
})

it('only opens web links without embedded credentials', () => {
  expect(isExternalWebUrl('https://github.com/project/releases')).toBe(true)
  for (const url of [
    'file:///etc/passwd',
    'javascript:alert(1)',
    'ms-settings:privacy',
    'https://user:pass@example.test/',
    'invalid'
  ]) {
    expect(isExternalWebUrl(url)).toBe(false)
  }
})
