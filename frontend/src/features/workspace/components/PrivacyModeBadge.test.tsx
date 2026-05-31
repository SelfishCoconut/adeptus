import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PrivacyModeBadge } from './PrivacyModeBadge'

describe('PrivacyModeBadge', () => {
  it('renders local_only badge with correct text', () => {
    render(<PrivacyModeBadge privacyMode="local_only" />)

    // Badge is a presentational pill — no role="status" (that lives on the outer banner).
    expect(
      screen.getByText('Local only — no data leaves the local network'),
    ).toBeInTheDocument()
  })

  it('renders cloud_enabled badge with correct text', () => {
    render(<PrivacyModeBadge privacyMode="cloud_enabled" />)

    expect(
      screen.getByText('Cloud enabled — data may leave the local network'),
    ).toBeInTheDocument()
  })

  it('local_only badge has green styling indicator', () => {
    const { container } = render(<PrivacyModeBadge privacyMode="local_only" />)

    const span = container.querySelector('span')
    expect(span?.className).toMatch(/green/)
  })

  it('cloud_enabled badge has amber styling indicator', () => {
    const { container } = render(<PrivacyModeBadge privacyMode="cloud_enabled" />)

    const span = container.querySelector('span')
    expect(span?.className).toMatch(/amber/)
  })
})
