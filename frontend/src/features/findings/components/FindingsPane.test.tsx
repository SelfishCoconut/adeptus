import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindingsPane } from './FindingsPane'
import type { Finding } from '../api'

// Stub the children so this test focuses on the pane's open/target wiring.
const editCallbacks: ((f: Finding) => void)[] = []
vi.mock('./FindingsList', () => ({
  FindingsList: ({ onEditFinding }: { onEditFinding: (f: Finding) => void }) => {
    editCallbacks.push(onEditFinding)
    return <div data-testid="findings-list" />
  },
}))
vi.mock('./FindingDialog', () => ({
  FindingDialog: ({ open, finding }: { open: boolean; finding?: Finding }) => (
    <div data-testid="finding-dialog" data-open={String(open)} data-finding={finding?.id ?? ''} />
  ),
}))

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeFinding(id = 'finding-9'): Finding {
  return {
    id,
    engagement_id: ENGAGEMENT_ID,
    title: 'XSS',
    description: '',
    severity: 'high',
    verification_status: 'unverified',
    remediation_status: 'open',
    node_id: null,
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('FindingsPane', () => {
  beforeEach(() => {
    editCallbacks.length = 0
  })

  it('opens the dialog in create mode when "New finding" is clicked', async () => {
    const user = userEvent.setup()
    render(<FindingsPane engagementId={ENGAGEMENT_ID} />)

    expect(screen.getByTestId('finding-dialog')).toHaveAttribute('data-open', 'false')
    await user.click(screen.getByRole('button', { name: 'New finding' }))

    const dialog = screen.getByTestId('finding-dialog')
    expect(dialog).toHaveAttribute('data-open', 'true')
    expect(dialog).toHaveAttribute('data-finding', '') // no target = create
  })

  it('opens the dialog in edit mode with the target finding', () => {
    render(<FindingsPane engagementId={ENGAGEMENT_ID} />)
    // Drive the list's onEditFinding callback (captured by the stub).
    act(() => editCallbacks[0](makeFinding('edit-target')))

    const dialog = screen.getByTestId('finding-dialog')
    expect(dialog).toHaveAttribute('data-open', 'true')
    expect(dialog).toHaveAttribute('data-finding', 'edit-target')
  })
})
