import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PrivacyModeBanner } from './PrivacyModeBanner'

describe('PrivacyModeBanner', () => {
  it('renders full-width banner for local_only with aria-live="polite"', () => {
    render(<PrivacyModeBanner privacyMode="local_only" />)

    // The outer banner container has role="status" and aria-live="polite"
    const banner = screen.getByRole('status', {
      name: (_, el) => el.getAttribute('aria-live') === 'polite',
    })
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveAttribute('aria-live', 'polite')
    expect(banner).toHaveTextContent('Local only — no data leaves the local network')
  })

  it('renders full-width banner for cloud_enabled with correct child content', () => {
    render(<PrivacyModeBanner privacyMode="cloud_enabled" />)

    const banner = screen.getAllByRole('status')[0]
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveTextContent('Cloud enabled — data may leave the local network')
  })
})
