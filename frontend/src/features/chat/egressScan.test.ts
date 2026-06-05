import { describe, expect, it } from 'vitest'

import { egressCategoryLabel, scanEgress } from './egressScan'

// Synthetic test vectors (not real secrets); each carries gitleaks:allow. The PEM header is
// assembled from two literals so the substring-based detect-private-key hook does not flag it.
const PRIVATE_KEY_HEADER = '-----BEGIN RSA ' + 'PRIVATE KEY-----'

describe('scanEgress', () => {
  it('matches an AWS access key', () => {
    expect(scanEgress('creds AKIAIOSFODNN7EXAMPLE end')).toContain('aws_access_key') // gitleaks:allow
  })

  it('matches a PEM private-key block', () => {
    expect(scanEgress(`key:\n${PRIVATE_KEY_HEADER}\nMIIB`)).toContain('private_key_block')
  })

  it('matches a JWT', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w' // gitleaks:allow
    expect(scanEgress(`token=${jwt}`)).toContain('jwt')
  })

  it('matches a password= assignment', () => {
    expect(scanEgress('login with password=hunter2')).toContain('password_assignment') // gitleaks:allow
  })

  it('matches a generic api-key assignment', () => {
    expect(scanEgress('api_key=AbCdEf0123456789ZZ')).toContain('generic_api_key') // gitleaks:allow
  })

  it('matches a bearer token', () => {
    expect(scanEgress('Authorization: Bearer abcDEF123456ghiJKL')).toContain('bearer_token') // gitleaks:allow
  })

  it('returns empty for ordinary prose', () => {
    expect(scanEgress('How do I test for SQL injection on the login form?')).toEqual([])
  })

  it('does not flag the bare word "password" without an assignment', () => {
    expect(scanEgress('I forgot my password, please help.')).toEqual([])
  })

  it('deduplicates and reports each category once, in declaration order', () => {
    const content = 'AKIAIOSFODNN7EXAMPLE and AKIAABCDEFGH12345678 plus password=hunter2' // gitleaks:allow
    expect(scanEgress(content)).toEqual(['aws_access_key', 'password_assignment'])
  })

  it('returns category names, never the matched secret value (§5.5)', () => {
    const secret = 'AKIAIOSFODNN7EXAMPLE' // gitleaks:allow
    const result = scanEgress(`here: ${secret}`)
    expect(result).toEqual(['aws_access_key'])
    expect(result.join(' ')).not.toContain(secret)
  })
})

describe('egressCategoryLabel', () => {
  it('maps known categories to human-readable labels', () => {
    expect(egressCategoryLabel('aws_access_key')).toBe('AWS access key')
    expect(egressCategoryLabel('password_assignment')).toBe('password= assignment')
  })

  it('falls back to the raw name for an unknown category (forward-compatible)', () => {
    expect(egressCategoryLabel('future_pattern')).toBe('future_pattern')
  })
})
