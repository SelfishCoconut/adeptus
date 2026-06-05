import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { SendChatMessageResult } from '@/shared/api'
import { chatKeys, flattenChatPages, useChatMessages } from '../api'
import { useChatStream } from '../hooks/useChatStream'
import { useLowConfidenceThreshold } from '../hooks/useLowConfidenceThreshold'
import { ChatComposer } from './ChatComposer'
import { ChatMessageList } from './ChatMessageList'

interface ChatPanelProps {
  engagementId: string
  /** When true, the engagement is archived/read-only (§4). */
  archived?: boolean
}

/**
 * The left-pane AI chat for an engagement (§11.2). Composes the message list, the
 * composer, and the streaming hook:
 *
 *   composer send → POST → on 201 set the assistant id → useChatStream streams it →
 *   on done invalidate the conversation so the finalized content reloads.
 *
 * The stream hook's `error` (§5.1 "AI is unreachable") is surfaced inline by the list
 * for the in-flight message, and the refetched `failed` row keeps it visible afterward.
 */
export function ChatPanel({ engagementId, archived = false }: ChatPanelProps) {
  const [streamingId, setStreamingId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const messagesQuery = useChatMessages(engagementId)
  const stream = useChatStream(streamingId)
  const messages = flattenChatPages(messagesQuery.data)
  const threshold = useLowConfidenceThreshold(engagementId)

  // When a streamed turn finishes, pull the finalized assistant content. We do NOT clear
  // streamingId here (that would be a setState-in-effect): the list stops treating the
  // message as streaming once the refetched row is no longer `pending`, and the next send
  // overwrites streamingId with the new assistant id. The error case keeps the inline
  // offline notice until then; the refetched `failed` row carries the same notice.
  useEffect(() => {
    if (!streamingId || !stream.isDone) return
    void queryClient.invalidateQueries({ queryKey: chatKeys.conversation(engagementId) })
  }, [streamingId, stream.isDone, engagementId, queryClient])

  const handleSent = (result: SendChatMessageResult) => {
    setStreamingId(result.assistant_message.id)
  }

  const isStreaming = streamingId !== null && !stream.isDone

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <ChatMessageList
          engagementId={engagementId}
          messages={messages}
          streamingId={streamingId}
          streamingText={stream.text}
          streamError={stream.error}
          streamingPlan={stream.plan}
          threshold={threshold}
        />
      </div>
      <ChatComposer
        engagementId={engagementId}
        archived={archived}
        isStreaming={isStreaming}
        onSent={handleSent}
      />
    </div>
  )
}
