import { describe, expect, it } from 'vitest'
import { redactSecrets } from './redaction'

describe('redactSecrets', () => {
  it('redacts 7zip passwords and credential-looking fields', () => {
    const text = redactSecrets(
      'running 7zz x game.7z -pSuperSecret -y {"build_secret":"vbs_abc","password":"pw"}'
    )
    expect(text).not.toContain('SuperSecret')
    expect(text).not.toContain('vbs_abc')
    expect(text).not.toContain('"pw"')
  })

  it('redacts headers, query tokens and URL userinfo', () => {
    const text = redactSecrets(
      'X-API-Key: abc123 https://user:secret@example.test/meta.7z?access_token=tok'
    )
    expect(text).not.toContain('abc123')
    expect(text).not.toContain('secret@')
    expect(text).not.toContain('=tok')
  })
})
