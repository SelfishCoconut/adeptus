import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Claim } from '@/shared/api'
import { CertaintyBadge, LOW_CONFIDENCE_THRESHOLD } from './CertaintyBadge'

const claim = (certainty: number, text = 'service is likely Apache'): Claim => ({
  text,
  certainty,
  node_id: null,
})

describe('CertaintyBadge', () => {
  it('shows the claim text and percentage', () => {
    render(<CertaintyBadge claim={claim(60, 'likely Apache 2.4')} />)
    expect(screen.getByText('likely Apache 2.4')).toBeInTheDocument()
    expect(screen.getByText('(60% certain)')).toBeInTheDocument()
  })

  it('flags a low-confidence claim distinctly', () => {
    render(<CertaintyBadge claim={claim(LOW_CONFIDENCE_THRESHOLD - 1)} />)
    expect(screen.getByTestId('certainty-badge')).toHaveAttribute('data-low-confidence', 'true')
  })

  it('renders a high-confidence claim plainly', () => {
    render(<CertaintyBadge claim={claim(95)} />)
    expect(screen.getByTestId('certainty-badge')).toHaveAttribute('data-low-confidence', 'false')
  })

  it('treats exactly the threshold as not low-confidence', () => {
    render(<CertaintyBadge claim={claim(LOW_CONFIDENCE_THRESHOLD)} />)
    expect(screen.getByTestId('certainty-badge')).toHaveAttribute('data-low-confidence', 'false')
  })

  it('renders claim text verbatim (no redaction, §5.5)', () => {
    render(<CertaintyBadge claim={claim(40, 'the db password may be hunter2')} />)
    expect(screen.getByText('the db password may be hunter2')).toBeInTheDocument()
  })
})
