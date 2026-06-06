import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApprovalCard } from './ApprovalCard'
import type { AutonomousAction } from '../api'
import { api, type ApprovalRequest } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockPost = vi.mocked(api.POST)

const ENG = 'eng-1'

const request = (overrides: Partial<ApprovalRequest> = {}): ApprovalRequest =>
  ({
    id: 'req-1',
    engagement_id: ENG,
    chat_message_id: 'msg-1',
    initiator_user_id: 'user-1',
    server_name: 'shell-exec',
    tool_name: 'run',
    args: { cmd: 'hydra -P rockyou.txt' },
    preset_name: null,
    rationale: 'Brute-force the SSH login.',
    reasons: ['credential_attack'],
    status: 'pending',
    acted_by_user_id: null,
    acted_by_username: null,
    self_approved: null,
    tool_run_id: null,
    created_at: '2026-06-05T00:00:00Z',
    decided_at: null,
    ...overrides,
  }) as ApprovalRequest

function renderCard(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  mockPost.mockReset()
})

describe('ApprovalCard — gated', () => {
  it('shows both buttons + the reason label while pending', () => {
    renderCard(<ApprovalCard engagementId={ENG} request={request()} />)
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(screen.getByText('credential attack')).toBeInTheDocument()
  })

  it('renders the unclassified_manifest reason label', () => {
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['unclassified_manifest'] })} />)
    expect(screen.getByText('tool not classified in its manifest')).toBeInTheDocument()
  })

  it('renders the out_of_scope reason label', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({ reasons: ['out_of_scope'], out_of_scope_host: 'example.com' })}
      />,
    )
    expect(screen.getByText('target is outside the declared scope')).toBeInTheDocument()
  })

  it('shows the out-of-scope host and scope context', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({
          reasons: ['out_of_scope'],
          out_of_scope_host: 'example.com',
          scope_checked_against: 'juice-shop, 10.0.0.0/24, *.target.test',
        })}
      />,
    )
    const ctx = screen.getByTestId('scope-context')
    expect(ctx).toHaveTextContent('example.com is not in scope:')
    expect(ctx).toHaveTextContent('juice-shop, 10.0.0.0/24, *.target.test')
  })

  it('shows both danger and out_of_scope labels when combined', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({
          reasons: ['aggressive_scan', 'out_of_scope'],
          out_of_scope_host: 'example.com',
          scope_checked_against: 'juice-shop',
        })}
      />,
    )
    expect(screen.getByText('aggressive scan')).toBeInTheDocument()
    expect(screen.getByText('target is outside the declared scope')).toBeInTheDocument()
  })

  it('does not render scope context for a non-out-of-scope request', () => {
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['credential_attack'] })} />)
    expect(screen.queryByTestId('scope-context')).not.toBeInTheDocument()
  })

  it('renders gracefully when the out-of-scope host is set but scope text is null', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({
          reasons: ['out_of_scope'],
          out_of_scope_host: 'example.com',
          scope_checked_against: null,
        })}
      />,
    )
    const ctx = screen.getByTestId('scope-context')
    expect(ctx).toHaveTextContent('example.com is not in scope:')
    expect(ctx).toHaveTextContent('(scope not recorded)')
  })

  it('approve → shows "Approved by @user" and hides the buttons', async () => {
    mockPost.mockResolvedValue({
      data: request({ status: 'approved', acted_by_username: 'alice', self_approved: true }),
    } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() =>
      expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @alice'),
    )
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('reject → shows "Rejected by @user"', async () => {
    mockPost.mockResolvedValue({
      data: request({ status: 'rejected', acted_by_username: 'bob' }),
    } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    await waitFor(() =>
      expect(screen.getByTestId('approval-decision')).toHaveTextContent('Rejected by @bob'),
    )
  })

  it('renders a decided request (another member acted) with no buttons', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({ status: 'approved', acted_by_username: 'carol' })}
      />,
    )
    expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @carol')
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('surfaces a 409 already-decided conflict', async () => {
    mockPost.mockResolvedValue({
      error: { reason: 'already_decided', status: 'rejected' },
      response: { status: 409 },
    } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() =>
      expect(screen.getByTestId('approval-conflict')).toHaveTextContent(
        'Already rejected by another member',
      ),
    )
  })
})

describe('ApprovalCard — always allow (standing autonomy)', () => {
  it('offers "Always allow" for a delegable category while pending', () => {
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['credential_attack'] })} />)
    expect(screen.getByTestId('always-allow-credential_attack')).toBeInTheDocument()
  })

  it('does not offer "Always allow" for an unclassified_manifest card', () => {
    renderCard(
      <ApprovalCard engagementId={ENG} request={request({ reasons: ['unclassified_manifest'] })} />,
    )
    expect(screen.queryByTestId('always-allow-row')).not.toBeInTheDocument()
  })

  it('does not offer "Always allow" when any reason is non-delegable', () => {
    renderCard(
      <ApprovalCard
        engagementId={ENG}
        request={request({ reasons: ['aggressive_scan', 'unclassified_manifest'] })}
      />,
    )
    expect(screen.queryByTestId('always-allow-row')).not.toBeInTheDocument()
  })

  it('grants standing autonomy then approves the current request', async () => {
    mockPost
      .mockResolvedValueOnce({ data: { id: 'g1', reason: 'credential_attack' } } as never)
      .mockResolvedValueOnce({
        data: request({ status: 'approved', acted_by_username: 'me', self_approved: true }),
      } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['credential_attack'] })} />)
    await userEvent.click(screen.getByTestId('always-allow-credential_attack'))
    await waitFor(() =>
      expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @me'),
    )
    expect(mockPost.mock.calls[0][0]).toContain('/autonomy-grants')
    expect(mockPost.mock.calls[1][0]).toContain('/approve')
  })

  it('approves the current request even when the grant 409s (already delegated)', async () => {
    mockPost
      .mockResolvedValueOnce({ error: { detail: 'already active' }, response: { status: 409 } } as never)
      .mockResolvedValueOnce({
        data: request({ status: 'approved', acted_by_username: 'me', self_approved: true }),
      } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['credential_attack'] })} />)
    await userEvent.click(screen.getByTestId('always-allow-credential_attack'))
    await waitFor(() =>
      expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @me'),
    )
    // The conflict is not surfaced as a failure — the category is already covered.
    expect(screen.queryByTestId('grant-error')).not.toBeInTheDocument()
    expect(mockPost.mock.calls[1][0]).toContain('/approve')
  })

  it('out_of_scope requires an explicit louder confirm before granting', async () => {
    mockPost
      .mockResolvedValueOnce({ data: { id: 'g1', reason: 'out_of_scope' } } as never)
      .mockResolvedValueOnce({ data: request({ status: 'approved', acted_by_username: 'me' }) } as never)
    renderCard(<ApprovalCard engagementId={ENG} request={request({ reasons: ['out_of_scope'] })} />)
    await userEvent.click(screen.getByTestId('always-allow-out_of_scope'))
    // No grant fired yet — the louder confirm gates it (Risk 2).
    expect(mockPost).not.toHaveBeenCalled()
    expect(screen.getByTestId('out-of-scope-confirm')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('out-of-scope-confirm-grant'))
    await waitFor(() => expect(mockPost.mock.calls[0][0]).toContain('/autonomy-grants'))
  })
})

describe('ApprovalCard — autonomous', () => {
  const autonomousAction = (overrides: Partial<AutonomousAction> = {}): AutonomousAction => ({
    server_name: 'httpx-server',
    tool_name: 'httpx',
    args: { target: '10.0.0.5' },
    preset_name: null,
    rationale: 'passive recon',
    tool_run_id: 'run-1',
    ...overrides,
  })

  it('shows the "running automatically" variant with no buttons', () => {
    renderCard(<ApprovalCard engagementId={ENG} autonomous={autonomousAction()} />)
    expect(screen.getByText('running automatically')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('shows the standing-autonomy indicator for an auto-approved command', () => {
    renderCard(<ApprovalCard engagementId={ENG} autonomous={autonomousAction({ auto_approved: true })} />)
    expect(screen.getByText('auto-approved · standing autonomy')).toBeInTheDocument()
    expect(screen.queryByText('running automatically')).not.toBeInTheDocument()
  })
})
