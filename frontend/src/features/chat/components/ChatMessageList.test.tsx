import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatMessageList } from './ChatMessageList'
import type { ChatMessage } from '@/shared/api'

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
})

function renderList(props: Partial<Parameters<typeof ChatMessageList>[0]> = {}) {
  return render(
    <ChatMessageList
      messages={props.messages ?? []}
      streamingId={props.streamingId ?? null}
      streamingText={props.streamingText ?? ''}
      streamError={props.streamError ?? null}
    />,
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
})
