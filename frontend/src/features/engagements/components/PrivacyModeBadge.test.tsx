import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PrivacyModeBadge } from './PrivacyModeBadge'

describe('PrivacyModeBadge', () => {
  it('renders local_only badge with correct text and role', () => {
    render(<PrivacyModeBadge privacyMode="local_only" />)

    const badge = screen.getByRole('status')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveTextContent('Local only — no data leaves the local network')
  })

  it('renders cloud_enabled badge with correct text and role', () => {
    render(<PrivacyModeBadge privacyMode="cloud_enabled" />)

    const badge = screen.getByRole('status')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveTextContent('Cloud enabled — data may leave the local network')
  })

  it('local_only badge has green styling indicator', () => {
    render(<PrivacyModeBadge privacyMode="local_only" />)

    const badge = screen.getByRole('status')
    expect(badge.className).toMatch(/green/)
  })

  it('cloud_enabled badge has amber styling indicator', () => {
    render(<PrivacyModeBadge privacyMode="cloud_enabled" />)

    const badge = screen.getByRole('status')
    expect(badge.className).toMatch(/amber/)
  })
})
