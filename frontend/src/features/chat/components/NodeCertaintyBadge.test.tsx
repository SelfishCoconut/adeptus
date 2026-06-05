import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LOW_CONFIDENCE_THRESHOLD } from './CertaintyBadge'
import { NodeCertaintyBadge } from './NodeCertaintyBadge'

describe('NodeCertaintyBadge', () => {
  it('renders nothing when no certainty is given (node has no claim)', () => {
    const { container } = render(<NodeCertaintyBadge />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the percentage and flags low confidence', () => {
    render(<NodeCertaintyBadge certainty={LOW_CONFIDENCE_THRESHOLD - 10} />)
    const badge = screen.getByTestId('node-certainty-badge')
    expect(badge).toHaveTextContent(`${LOW_CONFIDENCE_THRESHOLD - 10}%`)
    expect(badge).toHaveAttribute('data-low-confidence', 'true')
  })

  it('renders a high-confidence node badge plainly', () => {
    render(<NodeCertaintyBadge certainty={95} />)
    expect(screen.getByTestId('node-certainty-badge')).toHaveAttribute(
      'data-low-confidence',
      'false',
    )
  })
})
