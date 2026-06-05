import { useEffect, useRef, useState } from 'react'

/**
 * A single frame pushed by the chat WebSocket endpoint (`WebSocketChatChunk`). Not
 * part of the OpenAPI document (WebSocket endpoints never are), so it is declared here
 * to match the backend contract:
 *   token — `data` carries an incremental slice of assistant text (append to buffer).
 *   done  — the stream finished; the assistant message is persisted complete.
 *   error — `message` carries a stable, non-leaky reason (e.g. the model being offline).
 */
interface WebSocketChatChunk {
  type: 'token' | 'done' | 'error'
  data?: string
  message?: string
}

export interface ChatStream {
  /** Accumulated assistant text for the in-flight message. */
  text: string
  /** True once a `done` frame has arrived (the turn finished successfully). */
  isDone: boolean
  /** A stable error reason once an `error` frame arrives, else null (§5.1 offline). */
  error: string | null
}

/** Build the ws(s):// URL for a chat message, mirroring the same-origin API client. */
function buildWsUrl(assistantMessageId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || window.location.origin
  const wsBase = base.replace(/^http/, 'ws')
  return `${wsBase}/ws/chat/${assistantMessageId}`
}

/**
 * Stream an assistant reply over WebSocket.
 *
 * Opens a socket when `assistantMessageId` is non-null, appends each `token` frame to a
 * text buffer, resolves `isDone` on `done`, and surfaces a stable reason on `error`. The
 * socket is closed on `done`, on `error`, and on unmount or when the id changes. A
 * completed/failed message replays from the server (the backend sends the stored content
 * then `done`, or the stored reason as `error`), so reconnects are safe.
 */
export function useChatStream(assistantMessageId: string | null): ChatStream {
  const [text, setText] = useState('')
  const [isDone, setIsDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const socketRef = useRef<WebSocket | null>(null)

  // Reset the buffer when we (re)target a message. Adjusting state during render —
  // rather than in an effect — avoids a wasted render pass with stale text.
  const [trackedId, setTrackedId] = useState(assistantMessageId)
  if (trackedId !== assistantMessageId) {
    setTrackedId(assistantMessageId)
    setText('')
    setIsDone(false)
    setError(null)
  }

  useEffect(() => {
    if (!assistantMessageId) return

    const socket = new WebSocket(buildWsUrl(assistantMessageId))
    socketRef.current = socket

    socket.onmessage = (event: MessageEvent) => {
      let chunk: WebSocketChatChunk
      try {
        chunk = JSON.parse(event.data as string) as WebSocketChatChunk
      } catch {
        return
      }

      switch (chunk.type) {
        case 'token':
          setText((prev) => prev + (chunk.data ?? ''))
          break
        case 'done':
          setIsDone(true)
          socket.close()
          break
        case 'error':
          setError(chunk.message ?? 'AI is unreachable — local model is offline')
          setIsDone(true)
          socket.close()
          break
      }
    }

    return () => {
      socket.onmessage = null
      socket.close()
      socketRef.current = null
    }
  }, [assistantMessageId])

  return { text, isDone, error }
}
