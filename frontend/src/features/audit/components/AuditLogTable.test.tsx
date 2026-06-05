import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuditLogTable } from './AuditLogTable'
import { useEngagementAudit } from '../api'
import type { AuditEntry } from '@/shared/api'

vi.mock('../api', () => ({
  useEngagementAudit: vi.fn(),
}))

const mockedUseEngagementAudit = vi.mocked(useEngagementAudit)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function entry(overrides: Partial<AuditEntry>): AuditEntry {
  return {
    id: '00000000-0000-0000-0000-0000000000aa',
    seq: 1,
    action: 'login',
    actor_user_id: '11111111-2222-3333-4444-555555555555',
    engagement_id: null,
    target_type: null,
    target_id: null,
    self_approved: null,
    payload: {},
    created_at: '2026-06-05T00:00:00Z',
    prev_hash: '0'.repeat(64),
    entry_hash: 'a'.repeat(64),
    ...overrides,
  }
}

function queryResult(overrides: Partial<ReturnType<typeof useEngagementAudit>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useEngagementAudit>
}

function page(items: AuditEntry[], next_cursor: string | null = null) {
  return { data: { pages: [{ items, next_cursor }], pageParams: [null] } }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AuditLogTable', () => {
  it('renders a row per entry with action, actor and self_approved', () => {
    mockedUseEngagementAudit.mockReturnValue(
      queryResult(
        page([
          entry({ seq: 2, action: 'graph_node_created', target_type: 'node', target_id: 'deadbeef-aa' }),
          entry({ seq: 1, action: 'approval_granted', self_approved: true }),
        ]),
      ),
    )
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)

    // Action names also appear as <option>s in the filter, so scope to the table body.
    const table = within(screen.getByRole('table'))
    expect(table.getByText('graph_node_created')).toBeInTheDocument()
    expect(table.getByText('approval_granted')).toBeInTheDocument()
    expect(table.getByText('self')).toBeInTheDocument()
    // Self-approved column header exists even with no approval rows (Slice 16 fills values).
    expect(table.getByText('Self-approved')).toBeInTheDocument()
  })

  it('passes the selected action filter to the hook', async () => {
    mockedUseEngagementAudit.mockReturnValue(queryResult(page([entry({})])))
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)

    await userEvent.selectOptions(screen.getByLabelText('Filter by action'), 'tool_run')

    expect(mockedUseEngagementAudit).toHaveBeenLastCalledWith(
      ENGAGEMENT_ID,
      expect.objectContaining({ action: 'tool_run' }),
    )
  })

  it('toggling self-approved sets the filter to true', async () => {
    mockedUseEngagementAudit.mockReturnValue(queryResult(page([entry({})])))
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)

    await userEvent.click(screen.getByLabelText('Self-approved only'))

    expect(mockedUseEngagementAudit).toHaveBeenLastCalledWith(
      ENGAGEMENT_ID,
      expect.objectContaining({ selfApproved: true }),
    )
  })

  it('shows Load more when there is a next page and calls fetchNextPage', async () => {
    const fetchNextPage = vi.fn()
    mockedUseEngagementAudit.mockReturnValue(
      queryResult({ ...page([entry({})], 'CURSOR1'), hasNextPage: true, fetchNextPage }),
    )
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)

    const button = screen.getByRole('button', { name: 'Load more' })
    await userEvent.click(button)
    expect(fetchNextPage).toHaveBeenCalled()
  })

  it('renders an empty state when there are no entries', () => {
    mockedUseEngagementAudit.mockReturnValue(queryResult(page([])))
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByText('No audit entries.')).toBeInTheDocument()
  })

  it('renders an error state', () => {
    mockedUseEngagementAudit.mockReturnValue(queryResult({ isError: true }))
    render(<AuditLogTable engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load audit log.')
  })
})
