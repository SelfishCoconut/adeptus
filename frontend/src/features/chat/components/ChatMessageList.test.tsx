import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatMessageList } from './ChatMessageList'
import type { ApprovalRequest, ChatMessage } from '@/shared/api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

const msg = (
  id: string,
  role: 'user' | 'assistant',
  content: string,
  status: ChatMessage['status'] = 'complete',
): ChatMessage => ({
  id,
  engagement_id: ENGAGEMENT_ID,
  role,
  content,
  status,
  created_at: '2026-01-01T00:00:00Z',
})

beforeEach(() => {
  // jsdom does not implement scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn()
  mockGet.mockReset()
})

function renderList(props: Partial<Parameters<typeof ChatMessageList>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return render(
    <ChatMessageList
      engagementId={props.engagementId ?? ENGAGEMENT_ID}
      messages={props.messages ?? []}
      streamingId={props.streamingId ?? null}
      streamingText={props.streamingText ?? ''}
      streamError={props.streamError ?? null}
      streamingPlan={props.streamingPlan ?? []}
      streamingApprovalRequests={props.streamingApprovalRequests}
      streamingAutonomousActions={props.streamingAutonomousActions}
    />,
    { wrapper: Wrapper },
  )
}

describe('ChatMessageList', () => {
  it('shows the empty-state prompt when there are no messages', () => {
    renderList()
    expect(screen.getByText(/ask the local ai/i)).toBeInTheDocument()
  })

  it('renders user and assistant rows', () => {
    renderList({
      messages: [msg('u1', 'user', 'what is sqli?'), msg('a1', 'assistant', 'It is an injection.')],
    })
    expect(screen.getByText('what is sqli?')).toBeInTheDocument()
    expect(screen.getByText('It is an injection.')).toBeInTheDocument()
  })

  it('shows the persona chip on an assistant turn that recorded a persona (§5.3)', () => {
    renderList({
      messages: [{ ...msg('a1', 'assistant', 'recon answer'), persona_name: 'Recon' }],
    })
    expect(screen.getByTestId('persona-chip')).toHaveTextContent('Recon')
  })

  it('omits the persona chip on user rows and on assistant turns with no persona', () => {
    renderList({
      messages: [
        { ...msg('u1', 'user', 'hi'), persona_name: null },
        msg('a1', 'assistant', 'an answer with no recorded persona'),
      ],
    })
    expect(screen.queryByTestId('persona-chip')).toBeNull()
  })

  it('renders completed assistant content as Markdown', () => {
    const { container } = renderList({
      messages: [msg('a1', 'assistant', 'Use **sqlmap** to test.')],
    })
    // react-markdown turns **sqlmap** into a <strong>.
    const strong = container.querySelector('strong')
    expect(strong).not.toBeNull()
    expect(strong).toHaveTextContent('sqlmap')
  })

  it('shows the live streaming text for the in-flight assistant message', () => {
    renderList({
      messages: [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'pending')],
      streamingId: 'a1',
      streamingText: 'Strea',
    })
    expect(screen.getByText('Strea')).toBeInTheDocument()
  })

  it('shows the offline state when the streaming turn errors', () => {
    renderList({
      messages: [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'pending')],
      streamingId: 'a1',
      streamError: 'AI is unreachable — local model is offline',
    })
    expect(screen.getByRole('alert')).toHaveTextContent(/unreachable/i)
  })

  it('shows the failed/offline state for a historical failed turn', () => {
    renderList({
      messages: [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'failed')],
    })
    expect(screen.getByRole('alert')).toHaveTextContent(/unreachable/i)
  })

  it('toggles the debug panel for the right assistant message', async () => {
    mockGet.mockResolvedValue({
      data: {
        message_id: 'a1',
        model: 'qwen3.5:9b',
        status: 'complete',
        nodes: [{ id: 'n1', type: 'host', label: '10.0.0.5', reasons: ['pinned'] }],
        edges: [],
        context_block: '## Relevant graph subset',
        raw_prompt: '[system]\n...',
        model_output: 'answer',
      },
      response: { status: 200 },
    } as never)

    const user = userEvent.setup()
    renderList({ messages: [msg('a1', 'assistant', 'an answer')] })

    // Panel is not mounted until the Debug toggle is clicked (lazy).
    expect(screen.queryByLabelText('AI debug panel')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Debug' }))

    expect(await screen.findByLabelText('AI debug panel')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug',
      expect.objectContaining({
        params: { path: { engagement_id: ENGAGEMENT_ID, message_id: 'a1' } },
      }),
    )
  })

  it('does not offer a Debug toggle for a pending assistant turn', () => {
    renderList({
      messages: [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'pending')],
    })
    expect(screen.queryByRole('button', { name: 'Debug' })).not.toBeInTheDocument()
  })

  it('renders inline certainty badges for a completed turn with claims', () => {
    const assistant: ChatMessage = {
      ...msg('a1', 'assistant', 'It is likely Apache.'),
      claims: [
        { text: 'service is Apache', certainty: 55, node_id: null },
        { text: 'patched recently', certainty: 90, node_id: null },
      ],
    }
    renderList({ messages: [assistant] })

    const badges = screen.getAllByTestId('certainty-badge')
    expect(badges).toHaveLength(2)
    expect(screen.getByText('(55% certain)')).toBeInTheDocument()
    expect(badges[0]).toHaveAttribute('data-low-confidence', 'true')
    expect(badges[1]).toHaveAttribute('data-low-confidence', 'false')
  })

  it('renders no claim badges when the turn has none', () => {
    renderList({ messages: [msg('a1', 'assistant', 'plain answer')] })
    expect(screen.queryByTestId('claim-badges')).not.toBeInTheDocument()
  })

  it('renders the Plan panel for the latest assistant turn from history', () => {
    const assistant: ChatMessage = {
      ...msg('a1', 'assistant', 'an answer'),
      plan: [{ step: 'Enumerate the login endpoint', status: 'done' }],
    }
    renderList({ messages: [assistant] })
    expect(screen.getByTestId('plan-panel')).toBeInTheDocument()
    expect(screen.getByText('Enumerate the login endpoint')).toBeInTheDocument()
  })

  it('shows the Plan panel only for the LATEST assistant turn', () => {
    const earlier: ChatMessage = {
      ...msg('a1', 'assistant', 'first answer'),
      plan: [{ step: 'old plan step', status: 'done' }],
    }
    const latest: ChatMessage = {
      ...msg('a2', 'assistant', 'second answer'),
      plan: [{ step: 'current plan step', status: 'in_progress' }],
    }
    renderList({ messages: [earlier, latest] })
    expect(screen.getByText('current plan step')).toBeInTheDocument()
    expect(screen.queryByText('old plan step')).not.toBeInTheDocument()
  })

  it('shows the live plan above the still-streaming bubble on done', () => {
    renderList({
      messages: [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'pending')],
      streamingId: 'a1',
      streamingText: 'Working…',
      streamingPlan: [{ step: 'Test SQLi', status: 'in_progress' }],
    })
    expect(screen.getByTestId('plan-panel')).toBeInTheDocument()
    expect(screen.getByText('Test SQLi')).toBeInTheDocument()
    expect(screen.getByText('Working…')).toBeInTheDocument()
  })
})


// --- Slice 16: inline approval / autonomous cards ---

const approvalRequest = (overrides: Partial<ApprovalRequest> = {}): ApprovalRequest =>
  ({
    id: 'req-1',
    engagement_id: ENGAGEMENT_ID,
    chat_message_id: 'a1',
    initiator_user_id: 'u1',
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

describe('ChatMessageList — approval cards (Slice 16)', () => {
  it('renders a pending approval card from a finalized turn (reload)', () => {
    const assistant = {
      ...msg('a1', 'assistant', 'I will brute-force it.'),
      approval_requests: [approvalRequest()],
    } as ChatMessage
    renderList({ messages: [assistant] })
    expect(screen.getByText('needs approval')).toBeInTheDocument()
    expect(screen.getByText('credential attack')).toBeInTheDocument()
  })

  it('renders a "Approved by @user" decided card after a refetch', () => {
    const assistant = {
      ...msg('a1', 'assistant', 'done'),
      approval_requests: [
        approvalRequest({ status: 'approved', acted_by_username: 'second', self_approved: false }),
      ],
    } as ChatMessage
    renderList({ messages: [assistant] })
    expect(screen.getByTestId('approval-decision')).toHaveTextContent('Approved by @second')
  })

  it('renders a gated card mid-stream from a proposed_action frame', () => {
    const pending = msg('a1', 'assistant', '', 'pending')
    renderList({
      messages: [pending],
      streamingId: 'a1',
      streamingText: 'Proposing…',
      streamingApprovalRequests: [approvalRequest()],
    })
    expect(screen.getByText('needs approval')).toBeInTheDocument()
  })

  it('renders the "running automatically" variant for an autonomous command mid-stream', () => {
    const pending = msg('a1', 'assistant', '', 'pending')
    renderList({
      messages: [pending],
      streamingId: 'a1',
      streamingText: 'Running…',
      streamingAutonomousActions: [
        {
          server_name: 'httpx-server',
          tool_name: 'httpx',
          args: { target: 'sandbox.test' },
          preset_name: null,
          rationale: 'recon',
          tool_run_id: 'run-1',
        },
      ],
    })
    expect(screen.getByText('running automatically')).toBeInTheDocument()
  })
})
