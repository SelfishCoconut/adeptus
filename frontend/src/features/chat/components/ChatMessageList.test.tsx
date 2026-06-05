import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatMessageList } from './ChatMessageList'
import type { ChatMessage } from '@/shared/api'
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
})
