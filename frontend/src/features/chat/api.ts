import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'
import {
  api,
  type ChatMessage,
  type ChatMessagePage,
  type ChatTurnDebug,
  type SendChatMessageResult,
} from '@/shared/api'

// --- Query keys ---

export const chatKeys = {
  all: ['chat'] as const,
  conversation: (engagementId: string) => ['chat', engagementId] as const,
  debug: (engagementId: string, messageId: string | null) =>
    ['chat', engagementId, 'debug', messageId] as const,
}

/**
 * Input for {@link useSendChatMessage}. The three id lists are the client-supplied arms
 * of the §5.3 "relevant subset" union (pinned / recently-touched / @-mentioned). They are
 * optional so callers that don't yet track graph context send an empty union.
 */
export interface SendChatMessageInput {
  content: string
  pinnedNodeIds?: string[]
  recentNodeIds?: string[]
  mentionedNodeIds?: string[]
}

const PAGE_LIMIT = 50

/** What TanStack Query stores in the cache for this infinite query. */
type InfiniteChatData = InfiniteData<ChatMessagePage>

// --- Queries ---

/**
 * Load the caller's private conversation for an engagement (keyset pagination).
 * Page 0 is the most recent batch (oldest-first within the page); each subsequent
 * page (fetched via `next_cursor`) is an older batch — infinite scroll-up.
 */
export function useChatMessages(engagementId: string, options?: { enabled?: boolean }) {
  return useInfiniteQuery<ChatMessagePage>({
    queryKey: chatKeys.conversation(engagementId),
    enabled: (options?.enabled ?? true) && Boolean(engagementId),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/chat/messages',
        {
          params: {
            path: { engagement_id: engagementId },
            query: { limit: PAGE_LIMIT, ...(pageParam ? { cursor: pageParam as string } : {}) },
          },
        },
      )
      if (error || !data) throw new Error('Failed to load chat messages')
      return data
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })
}

/** Flatten the infinite pages into a single oldest-first message list for rendering. */
export function flattenChatPages(data: InfiniteChatData | undefined): ChatMessage[] {
  if (!data) return []
  // pages[0] is the newest batch; later pages (from fetchNextPage) are older batches.
  // Reverse so the oldest batch renders at the top, then flatten (each page is already
  // oldest-first internally).
  return [...data.pages].reverse().flatMap((page) => page.items)
}

// --- Mutations ---

function optimisticMessage(
  engagementId: string,
  role: 'user' | 'assistant',
  content: string,
  status: 'complete' | 'pending',
): ChatMessage {
  return {
    id: `optimistic-${role}-${crypto.randomUUID()}`,
    engagement_id: engagementId,
    role,
    content,
    status,
    created_at: new Date().toISOString(),
  }
}

/**
 * Send a user message. Optimistically appends the user message and an empty pending
 * assistant placeholder to the most recent page so the input feels instant; the
 * authoritative rows (with real ids) replace them when the conversation is refetched
 * on settle. The returned ids let the caller open the streaming WebSocket.
 */
export function useSendChatMessage(engagementId: string) {
  const queryClient = useQueryClient()
  const queryKey = chatKeys.conversation(engagementId)

  return useMutation<
    SendChatMessageResult,
    Error,
    SendChatMessageInput,
    { previous?: InfiniteChatData }
  >({
    mutationFn: async ({ content, pinnedNodeIds, recentNodeIds, mentionedNodeIds }) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/chat/messages',
        {
          params: { path: { engagement_id: engagementId } },
          body: {
            content,
            pinned_node_ids: pinnedNodeIds ?? [],
            recent_node_ids: recentNodeIds ?? [],
            mentioned_node_ids: mentionedNodeIds ?? [],
          },
        },
      )
      if (error || !data) throw new Error('Failed to send message')
      return data
    },
    onMutate: async ({ content }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<InfiniteChatData>(queryKey)

      const userMsg = optimisticMessage(engagementId, 'user', content, 'complete')
      const pendingMsg = optimisticMessage(engagementId, 'assistant', '', 'pending')

      queryClient.setQueryData<InfiniteChatData>(queryKey, (old) => {
        if (!old || old.pages.length === 0) {
          return {
            pages: [{ items: [userMsg, pendingMsg], next_cursor: null }],
            pageParams: [null],
          }
        }
        const pages = old.pages.slice()
        pages[0] = { ...pages[0], items: [...pages[0].items, userMsg, pendingMsg] }
        return { ...old, pages }
      })

      return { previous }
    },
    onError: (_err, _input, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey, context.previous)
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey })
    },
  })
}

/**
 * Lazily load the AI debug record (§14) for one assistant turn: the exact §5.3 relevant
 * subset of the graph injected, the raw prompt, and the model output. Disabled until a
 * `messageId` is provided (the panel is opened), so chat history loads stay lean and the
 * large prompt blob is only fetched on demand.
 */
export function useChatTurnDebug(engagementId: string, messageId: string | null) {
  return useQuery<ChatTurnDebug>({
    queryKey: chatKeys.debug(engagementId, messageId),
    enabled: Boolean(engagementId) && Boolean(messageId),
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug',
        {
          params: {
            path: { engagement_id: engagementId, message_id: messageId as string },
          },
        },
      )
      if (error || !data) throw new Error('Failed to load AI debug record')
      return data
    },
  })
}
