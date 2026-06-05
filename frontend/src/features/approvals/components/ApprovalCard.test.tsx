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

describe('ApprovalCard — autonomous', () => {
  it('shows the "running automatically" variant with no buttons', () => {
    const autonomous: AutonomousAction = {
      server_name: 'httpx-server',
      tool_name: 'httpx',
      args: { target: '10.0.0.5' },
      preset_name: null,
      rationale: 'passive recon',
      tool_run_id: 'run-1',
    }
    renderCard(<ApprovalCard engagementId={ENG} autonomous={autonomous} />)
    expect(screen.getByText('running automatically')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })
})
