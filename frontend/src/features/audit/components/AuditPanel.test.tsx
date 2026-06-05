import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AuditPanel } from './AuditPanel'

vi.mock('./AuditLogTable', () => ({
  AuditLogTable: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="audit-log-table" data-engagement-id={engagementId} />
  ),
}))

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

describe('AuditPanel', () => {
  it('renders a disclosure that embeds the audit log table for the engagement', () => {
    render(<AuditPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByText('Audit log')).toBeInTheDocument()
    expect(screen.getByTestId('audit-log-table')).toHaveAttribute(
      'data-engagement-id',
      ENGAGEMENT_ID,
    )
  })
})
