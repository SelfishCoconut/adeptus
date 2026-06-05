import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatComposer } from './ChatComposer'
import { api } from '@/shared/api'
import { usePinStore } from '@/features/graph/store/pinStore'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockPost = vi.mocked(api.POST)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function renderComposer(props: Partial<Parameters<typeof ChatComposer>[0]> = {}) {
  const onSent = props.onSent ?? vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  render(
    <ChatComposer
      engagementId={props.engagementId ?? ENGAGEMENT_ID}
      archived={props.archived ?? false}
      privacyMode={props.privacyMode ?? 'local_only'}
      isStreaming={props.isStreaming ?? false}
      onSent={onSent}
    />,
    { wrapper: Wrapper },
  )
  return { onSent }
}

beforeEach(() => {
  mockPost.mockReset()
  localStorage.clear()
  usePinStore.setState({ pinnedByEngagement: {} })
})

const sendResult = {
  user_message: {
    id: 'u1',
    engagement_id: ENGAGEMENT_ID,
    role: 'user',
    content: 'hello',
    status: 'complete',
    created_at: '2026-01-01T00:00:00Z',
  },
  assistant_message: {
    id: 'a1',
    engagement_id: ENGAGEMENT_ID,
    role: 'assistant',
    content: '',
    status: 'pending',
    created_at: '2026-01-01T00:00:00Z',
  },
}

describe('ChatComposer', () => {
  it('disables send while the input is empty', () => {
    renderComposer()
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
  })

  it('submits, clears the input, and notifies the parent on success', async () => {
    mockPost.mockResolvedValue({ data: sendResult, response: { status: 201 } } as never)

    const user = userEvent.setup()
    const { onSent } = renderComposer()
    const textarea = screen.getByLabelText(/message the ai/i)

    await user.type(textarea, 'hello')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(onSent).toHaveBeenCalledWith(sendResult))
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages',
      expect.objectContaining({
        body: {
          content: 'hello',
          pinned_node_ids: [],
          recent_node_ids: [],
          mentioned_node_ids: [],
          confirmed_egress: false,
        },
      }),
    )
    expect(textarea).toHaveValue('')
  })

  it('forwards the current pinned set as the §5.3 pinned arm', async () => {
    usePinStore.getState().togglePin(ENGAGEMENT_ID, 'node-1')
    usePinStore.getState().togglePin(ENGAGEMENT_ID, 'node-2')
    mockPost.mockResolvedValue({ data: sendResult, response: { status: 201 } } as never)

    const user = userEvent.setup()
    renderComposer()
    await user.type(screen.getByLabelText(/message the ai/i), 'against the box')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    const body = mockPost.mock.calls[0][1] as { body: { pinned_node_ids: string[] } }
    expect([...body.body.pinned_node_ids].sort()).toEqual(['node-1', 'node-2'])
  })

  it('disables input and send and shows a hint when archived', () => {
    renderComposer({ archived: true })
    expect(screen.getByLabelText(/message the ai/i)).toBeDisabled()
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
    expect(screen.getByText(/archived and read-only/i)).toBeInTheDocument()
  })

  it('disables send while a turn is streaming', async () => {
    const user = userEvent.setup()
    renderComposer({ isStreaming: true })
    await user.type(screen.getByLabelText(/message the ai/i), 'hi')
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
  })

  // --- §5.1 cloud egress pattern-friction (Slice 14) ---

  // Synthetic secret vector; carries gitleaks:allow.
  const SECRET = 'login with password=hunter2' // gitleaks:allow

  it('cloud + secret → shows the friction modal and does NOT send until confirm', async () => {
    const user = userEvent.setup()
    renderComposer({ privacyMode: 'cloud_enabled' })
    await user.type(screen.getByLabelText(/message the ai/i), SECRET)
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    expect(screen.getByRole('button', { name: /send anyway/i })).toBeInTheDocument()
    expect(mockPost).not.toHaveBeenCalled() // nothing left the machine yet
  })

  it('cloud + secret + confirm → sends with confirmed_egress=true', async () => {
    mockPost.mockResolvedValue({ data: sendResult, response: { status: 201 } } as never)
    const user = userEvent.setup()
    renderComposer({ privacyMode: 'cloud_enabled' })
    await user.type(screen.getByLabelText(/message the ai/i), SECRET)
    await user.click(screen.getByRole('button', { name: /^send$/i }))
    await user.click(screen.getByRole('button', { name: /send anyway/i }))

    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    const body = mockPost.mock.calls[0][1] as { body: { confirmed_egress: boolean } }
    expect(body.body.confirmed_egress).toBe(true)
  })

  it('cloud + clean text → sends directly, no modal', async () => {
    mockPost.mockResolvedValue({ data: sendResult, response: { status: 201 } } as never)
    const user = userEvent.setup()
    renderComposer({ privacyMode: 'cloud_enabled' })
    await user.type(screen.getByLabelText(/message the ai/i), 'what is sql injection?')
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /send anyway/i })).not.toBeInTheDocument()
    const body = mockPost.mock.calls[0][1] as { body: { confirmed_egress: boolean } }
    expect(body.body.confirmed_egress).toBe(false)
  })

  it('local_only + secret → sends directly, no modal (no egress to gate)', async () => {
    mockPost.mockResolvedValue({ data: sendResult, response: { status: 201 } } as never)
    const user = userEvent.setup()
    renderComposer({ privacyMode: 'local_only' })
    await user.type(screen.getByLabelText(/message the ai/i), SECRET)
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /send anyway/i })).not.toBeInTheDocument()
  })

  it('server 409 egress → opens the modal from server categories, retry sends confirmed', async () => {
    // The client pre-flight passes (clean text), but the authoritative server 409s — the modal
    // is surfaced from the server's matched_categories and the retry confirms (Risk 3).
    mockPost
      .mockResolvedValueOnce({
        error: { reason: 'egress_secret_flagged', matched_categories: ['aws_access_key'] },
        response: { status: 409 },
      } as never)
      .mockResolvedValueOnce({ data: sendResult, response: { status: 201 } } as never)

    const user = userEvent.setup()
    renderComposer({ privacyMode: 'cloud_enabled' })
    await user.type(screen.getByLabelText(/message the ai/i), 'looks clean to the client')
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    // First POST happened (client missed it); the server 409 re-opens the modal.
    await waitFor(() => expect(screen.getByText(/AWS access key/)).toBeInTheDocument())
    expect(mockPost).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /send anyway/i }))
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2))
    const retry = mockPost.mock.calls[1][1] as { body: { confirmed_egress: boolean } }
    expect(retry.body.confirmed_egress).toBe(true)
  })
})
