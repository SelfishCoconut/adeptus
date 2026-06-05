import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApprovalQueue } from './ApprovalQueue'
import { api, type ApprovalRequest } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

const ENG = 'eng-1'

const request = (id: string, overrides: Partial<ApprovalRequest> = {}): ApprovalRequest =>
  ({
    id,
    engagement_id: ENG,
    chat_message_id: 'msg-1',
    initiator_user_id: 'user-1',
    server_name: 'shell-exec',
    tool_name: 'run',
    args: { cmd: 'login-bruteforce' },
    preset_name: null,
    rationale: null,
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

function renderQueue() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return render(<ApprovalQueue engagementId={ENG} />, { wrapper: Wrapper })
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
})

describe('ApprovalQueue', () => {
  it('lists pending requests', async () => {
    mockGet.mockResolvedValue({
      data: { items: [request('req-1'), request('req-2')], next_cursor: null },
    } as never)
    renderQueue()
    await waitFor(() => expect(screen.getAllByTestId('approval-card')).toHaveLength(2))
    expect(screen.getByText(/Approvals \(2\)/)).toBeInTheDocument()
    // It asks the server only for pending requests.
    const call = mockGet.mock.calls[0][1] as { params: { query: Record<string, unknown> } }
    expect(call.params.query.status).toBe('pending')
  })

  it('shows the empty state when none are pending', async () => {
    mockGet.mockResolvedValue({ data: { items: [], next_cursor: null } } as never)
    renderQueue()
    await waitFor(() =>
      expect(screen.getByTestId('approval-queue-empty')).toBeInTheDocument(),
    )
  })

  it('approves a request from the queue', async () => {
    mockGet.mockResolvedValue({ data: { items: [request('req-1')], next_cursor: null } } as never)
    mockPost.mockResolvedValue({
      data: request('req-1', { status: 'approved', acted_by_username: 'me', self_approved: true }),
    } as never)
    renderQueue()
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() =>
      expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @me'),
    )
    expect(mockPost.mock.calls[0][0]).toContain('/approve')
  })

  it('shows a load failure', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'no' }, response: { status: 404 } } as never)
    renderQueue()
    await waitFor(() => expect(screen.getByText('Failed to load approvals.')).toBeInTheDocument())
  })
})
