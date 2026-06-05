import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPanel } from './ChatPanel'
import { api, type ChatMessage } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

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

// --- Fake WebSocket ---

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  close = vi.fn()
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }
}

// Server-side conversation state the mocked GET reflects; POST mutates it.
let serverItems: ChatMessage[] = []

function renderPanel() {
  Element.prototype.scrollIntoView = vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  render(<ChatPanel engagementId={ENGAGEMENT_ID} privacyMode="local_only" />, { wrapper: Wrapper })
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  mockGet.mockReset()
  mockPost.mockReset()
  serverItems = []
  mockGet.mockImplementation(
    async () => ({ data: { items: serverItems, next_cursor: null }, response: { status: 200 } }) as never,
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatPanel', () => {
  it('sends a message, streams the assistant reply, then settles to history', async () => {
    const user = userEvent.setup()
    const userMsg = msg('u1', 'user', 'what is sqli?')
    const pending = msg('a1', 'assistant', '', 'pending')
    mockPost.mockImplementation(async () => {
      serverItems = [userMsg, pending]
      return {
        data: { user_message: userMsg, assistant_message: pending },
        response: { status: 201 },
      } as never
    })

    renderPanel()
    await user.type(screen.getByLabelText(/message the ai/i), 'what is sqli?')
    await user.click(screen.getByRole('button', { name: /send/i }))

    // The user message shows (optimistic + refetch).
    await waitFor(() => expect(screen.getByText('what is sqli?')).toBeInTheDocument())

    // A socket opened for the assistant message id.
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
    expect(FakeWebSocket.instances[0].url).toContain('/ws/chat/a1')

    // Tokens stream into the live region.
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'token', data: 'It is ' })
      FakeWebSocket.instances[0].emit({ type: 'token', data: 'injection.' })
    })
    expect(screen.getByText('It is injection.')).toBeInTheDocument()

    // On done, history is refetched with the finalized assistant content.
    serverItems = [userMsg, msg('a1', 'assistant', 'It is injection.')]
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'done' })
    })
    await waitFor(() => {
      const matches = screen.getAllByText('It is injection.')
      expect(matches.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('shows the Plan panel when the stream done frame delivers a plan, then from history', async () => {
    const user = userEvent.setup()
    const userMsg = msg('u1', 'user', 'how should I test the login flow?')
    const pending = msg('a1', 'assistant', '', 'pending')
    mockPost.mockImplementation(async () => {
      serverItems = [userMsg, pending]
      return {
        data: { user_message: userMsg, assistant_message: pending },
        response: { status: 201 },
      } as never
    })

    renderPanel()
    await user.type(screen.getByLabelText(/message the ai/i), 'how should I test the login flow?')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))

    // The settled history row carries the parsed plan (ChatMessageRead.plan).
    serverItems = [
      userMsg,
      { ...msg('a1', 'assistant', 'Here is the approach.'), plan: [
        { step: 'Enumerate the login endpoint', status: 'done' },
        { step: 'Test for SQL injection', status: 'in_progress' },
      ] },
    ]
    act(() => {
      FakeWebSocket.instances[0].emit({
        type: 'done',
        plan: [
          { step: 'Enumerate the login endpoint', status: 'done' },
          { step: 'Test for SQL injection', status: 'in_progress' },
        ],
      })
    })

    await waitFor(() => expect(screen.getByTestId('plan-panel')).toBeInTheDocument())
    expect(screen.getByText('Test for SQL injection')).toBeInTheDocument()
  })

  it('shows the unreachable banner when the stream errors', async () => {
    const user = userEvent.setup()
    const userMsg = msg('u1', 'user', 'hi')
    const pending = msg('a1', 'assistant', '', 'pending')
    mockPost.mockImplementation(async () => {
      serverItems = [userMsg, pending]
      return {
        data: { user_message: userMsg, assistant_message: pending },
        response: { status: 201 },
      } as never
    })

    renderPanel()
    await user.type(screen.getByLabelText(/message the ai/i), 'hi')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
    act(() => {
      FakeWebSocket.instances[0].emit({
        type: 'error',
        message: 'AI is unreachable — local model is offline',
      })
    })

    expect(await screen.findByRole('alert')).toHaveTextContent(/unreachable/i)
  })
})
