import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import type { ChatMessage } from '@/shared/api'

interface ChatMessageListProps {
  /** The conversation so far, oldest-first. */
  messages: ChatMessage[]
  /** Id of the assistant message currently streaming, or null. */
  streamingId: string | null
  /** Accumulated tokens for the streaming message (live region). */
  streamingText: string
  /** Stable reason when the streaming turn failed/offline (§5.1), else null. */
  streamError: string | null
}

const OFFLINE_TEXT = 'AI is unreachable — local model is offline'

/** Inline failed/offline state for an assistant turn (§5.1). */
function OfflineNotice({ reason }: { reason: string }) {
  return (
    <p role="alert" className="text-sm text-destructive">
      {reason}
    </p>
  )
}

/** A user turn: right-aligned plain-text bubble (sent verbatim, never redacted). */
function UserRow({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        {message.content}
      </div>
    </div>
  )
}

/** An assistant turn: left-aligned bubble; Markdown when complete. */
function AssistantRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm text-foreground">
        {children}
      </div>
    </div>
  )
}

/**
 * Scrollable message list. Renders user vs assistant turns, the in-flight assistant
 * message as a streaming live region, completed assistant content as Markdown (§11.1),
 * and an inline failed/offline state for failed turns (§5.1). Auto-scrolls to the
 * newest message as content arrives.
 */
export function ChatMessageList({
  messages,
  streamingId,
  streamingText,
  streamError,
}: ChatMessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, streamingText, streamError])

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <p className="text-sm text-muted-foreground">
          Ask the local AI about this engagement.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-4" data-testid="chat-message-list">
      {messages.map((message) => {
        if (message.role === 'user') {
          return <UserRow key={message.id} message={message} />
        }

        // Treat the message as live only while it is still pending: once the refetched
        // row is complete/failed it renders as history (Markdown / offline notice), even
        // though streamingId may still point at it.
        const isStreaming = message.id === streamingId && message.status === 'pending'

        if (isStreaming && streamError) {
          return (
            <AssistantRow key={message.id}>
              <OfflineNotice reason={streamError} />
            </AssistantRow>
          )
        }

        if (isStreaming) {
          return (
            <AssistantRow key={message.id}>
              <div aria-live="polite" className="whitespace-pre-wrap">
                {streamingText || <span className="text-muted-foreground">…</span>}
              </div>
            </AssistantRow>
          )
        }

        if (message.status === 'failed') {
          return (
            <AssistantRow key={message.id}>
              <OfflineNotice reason={OFFLINE_TEXT} />
            </AssistantRow>
          )
        }

        if (message.status === 'pending') {
          return (
            <AssistantRow key={message.id}>
              <span className="text-muted-foreground">…</span>
            </AssistantRow>
          )
        }

        return (
          <AssistantRow key={message.id}>
            <div className="space-y-2 [&_code]:rounded [&_code]:bg-background [&_code]:px-1">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          </AssistantRow>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}
