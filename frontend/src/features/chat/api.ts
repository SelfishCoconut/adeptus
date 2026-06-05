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
  /**
   * The user saw the cloud egress-friction modal for THIS content and chose to send it
   * unmodified anyway (§5.1, Slice 14). Defaults false; consulted server-side only when the
   * engagement is cloud_enabled and the content matched a secret pattern.
   */
  confirmedEgress?: boolean
  /**
   * The persona whose system prompt should shape THIS turn (§5.3, Slice 15), chosen per send
   * so the user can switch persona mid-chat. Omitted ⇒ the server uses the `general` built-in;
   * a foreign/unknown id also falls back to general server-side (§17.1).
   */
  personaId?: string
}

/**
 * Thrown when the server refuses a cloud-enabled send with a 409 ``egress_secret_flagged``
 * (§5.1 pattern-friction). Carries the matched category NAMES so the composer can surface the
 * friction modal even if its client pre-flight missed the pattern (client/server drift, Risk 3)
 * and retry with ``confirmedEgress: true``.
 */
export class EgressConfirmationRequiredError extends Error {
  readonly categories: string[]
  constructor(categories: string[]) {
    super('Cloud egress confirmation required')
    this.name = 'EgressConfirmationRequiredError'
    this.categories = categories
  }
}

/** Extract the matched categories from a 409 body iff it is the egress-friction case. */
function egressCategoriesFrom(error: unknown): string[] | null {
  if (!error || typeof error !== 'object') return null
  const body = error as Record<string, unknown>
  if (body.reason !== 'egress_secret_flagged') return null
  const cats = body.matched_categories
  return Array.isArray(cats) ? cats.filter((c): c is string => typeof c === 'string') : []
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
    mutationFn: async ({
      content,
      pinnedNodeIds,
      recentNodeIds,
      mentionedNodeIds,
      confirmedEgress,
      personaId,
    }) => {
      const { data, error, response } = await api.POST(
        '/api/v1/engagements/{engagement_id}/chat/messages',
        {
          params: { path: { engagement_id: engagementId } },
          body: {
            content,
            pinned_node_ids: pinnedNodeIds ?? [],
            recent_node_ids: recentNodeIds ?? [],
            mentioned_node_ids: mentionedNodeIds ?? [],
            confirmed_egress: confirmedEgress ?? false,
            // Included only when a persona is selected; omitted ⇒ server uses general.
            ...(personaId ? { persona_id: personaId } : {}),
          },
        },
      )
      if (error || !data) {
        const categories = response?.status === 409 ? egressCategoriesFrom(error) : null
        if (categories) throw new EgressConfirmationRequiredError(categories)
        throw new Error('Failed to send message')
      }
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
            // Optimistic empty-cache page: the real threshold arrives on the settle refetch;
            // 70 (the frontend default) is the safe placeholder until then.
            pages: [
              { items: [userMsg, pendingMsg], next_cursor: null, low_confidence_threshold: 70 },
            ],
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
