import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import {
  chatKeys,
  flattenChatPages,
  useChatMessages,
  useChatTurnDebug,
  useSendChatMessage,
} from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)
const resolvePost = (value: FetchResult) => mockPost.mockResolvedValue(value as never)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

const message = (id: string, role: 'user' | 'assistant', content: string) => ({
  id,
  engagement_id: ENGAGEMENT_ID,
  role,
  content,
  status: 'complete' as const,
  created_at: '2026-01-01T00:00:00Z',
})

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
})

describe('chatKeys', () => {
  it('namespaces the conversation by engagement', () => {
    expect(chatKeys.conversation(ENGAGEMENT_ID)).toEqual(['chat', ENGAGEMENT_ID])
  })
})

describe('useChatMessages', () => {
  it('loads the first page on success', async () => {
    resolveGet({
      data: { items: [message('m1', 'user', 'hi')], next_cursor: null },
      response: { status: 200 },
    })
    const { result } = renderHook(() => useChatMessages(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(flattenChatPages(result.current.data)).toHaveLength(1)
    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages',
      expect.objectContaining({
        params: expect.objectContaining({ path: { engagement_id: ENGAGEMENT_ID } }),
      }),
    )
  })

  it('paginates via next_cursor', async () => {
    mockGet
      .mockResolvedValueOnce({
        data: { items: [message('m2', 'user', 'newer')], next_cursor: 'cursor-older' },
        response: { status: 200 },
      } as never)
      .mockResolvedValueOnce({
        data: { items: [message('m1', 'user', 'older')], next_cursor: null },
        response: { status: 200 },
      } as never)

    const { result } = renderHook(() => useChatMessages(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)

    await result.current.fetchNextPage()
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2))
    expect(result.current.hasNextPage).toBe(false)

    // Second GET carried the cursor from page 1.
    expect(mockGet).toHaveBeenLastCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages',
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({ cursor: 'cursor-older' }),
        }),
      }),
    )
  })

  it('is disabled without an engagement id', () => {
    const { result } = renderHook(() => useChatMessages(''), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGet).not.toHaveBeenCalled()
  })
})

describe('useSendChatMessage', () => {
  it('surfaces both returned message ids', async () => {
    resolveGet({ data: { items: [], next_cursor: null }, response: { status: 200 } })
    resolvePost({
      data: {
        user_message: message('user-1', 'user', 'hello'),
        assistant_message: { ...message('assistant-1', 'assistant', ''), status: 'pending' },
      },
      response: { status: 201 },
    })

    const { result } = renderHook(() => useSendChatMessage(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    const sent = await result.current.mutateAsync({ content: 'hello' })
    expect(sent.user_message.id).toBe('user-1')
    expect(sent.assistant_message.id).toBe('assistant-1')
    expect(sent.assistant_message.status).toBe('pending')
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages',
      expect.objectContaining({
        body: {
          content: 'hello',
          pinned_node_ids: [],
          recent_node_ids: [],
          mentioned_node_ids: [],
        },
      }),
    )
  })

  it('forwards the §5.3 node-id union in the POST body', async () => {
    resolveGet({ data: { items: [], next_cursor: null }, response: { status: 200 } })
    resolvePost({
      data: {
        user_message: message('user-1', 'user', 'hello'),
        assistant_message: { ...message('assistant-1', 'assistant', ''), status: 'pending' },
      },
      response: { status: 201 },
    })

    const { result } = renderHook(() => useSendChatMessage(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await result.current.mutateAsync({
      content: 'hello',
      pinnedNodeIds: ['node-a'],
      recentNodeIds: ['node-a', 'node-b'],
    })
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages',
      expect.objectContaining({
        body: {
          content: 'hello',
          pinned_node_ids: ['node-a'],
          recent_node_ids: ['node-a', 'node-b'],
          mentioned_node_ids: [],
        },
      }),
    )
  })

  it('surfaces a 404 as an error', async () => {
    resolveGet({ data: { items: [], next_cursor: null }, response: { status: 200 } })
    resolvePost({ error: { detail: 'not found' }, response: { status: 404 } })

    const { result } = renderHook(() => useSendChatMessage(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await expect(result.current.mutateAsync({ content: 'hello' })).rejects.toBeInstanceOf(Error)
  })
})

describe('useChatTurnDebug', () => {
  const MESSAGE_ID = 'assistant-1'

  it('is disabled until a message id is provided', () => {
    const { result } = renderHook(() => useChatTurnDebug(ENGAGEMENT_ID, null), {
      wrapper: createWrapper(),
    })
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('loads the debug record once enabled', async () => {
    resolveGet({
      data: {
        message_id: MESSAGE_ID,
        model: 'qwen3.5:9b',
        status: 'complete',
        nodes: [{ id: 'n1', type: 'host', label: 'box', reasons: ['pinned'] }],
        edges: [],
        context_block: '## Relevant graph subset',
        raw_prompt: '[system]\n...',
        model_output: 'answer',
      },
      response: { status: 200 },
    })

    const { result } = renderHook(() => useChatTurnDebug(ENGAGEMENT_ID, MESSAGE_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.nodes).toHaveLength(1)
    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug',
      expect.objectContaining({
        params: { path: { engagement_id: ENGAGEMENT_ID, message_id: MESSAGE_ID } },
      }),
    )
  })

  it('surfaces a 404 as an error', async () => {
    resolveGet({ error: { detail: 'not found' }, response: { status: 404 } })

    const { result } = renderHook(() => useChatTurnDebug(ENGAGEMENT_ID, MESSAGE_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})
