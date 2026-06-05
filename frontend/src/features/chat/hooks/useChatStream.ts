import { useEffect, useRef, useState } from 'react'
import type { Claim, PlanStep } from '@/shared/api'

/**
 * A single frame pushed by the chat WebSocket endpoint (`WebSocketChatChunk`). Not
 * part of the OpenAPI document (WebSocket endpoints never are), so it is declared here
 * to match the backend contract:
 *   token — `data` carries an incremental slice of assistant text (append to buffer).
 *   done  — the stream finished; the assistant message is persisted complete. `plan` and
 *           `claims` (Slice 13) carry the turn's parsed running plan + certainty claims.
 *   error — `message` carries a stable, non-leaky reason (e.g. the model being offline).
 */
interface WebSocketChatChunk {
  type: 'token' | 'done' | 'error'
  data?: string
  message?: string
  plan?: PlanStep[]
  claims?: Claim[]
}

export interface ChatStream {
  /** Accumulated assistant text for the in-flight message (block-stripped prose). */
  text: string
  /** True once a `done` frame has arrived (the turn finished successfully). */
  isDone: boolean
  /** A stable error reason once an `error` frame arrives, else null (§5.1 offline). */
  error: string | null
  /** The turn's running plan, resolved on the `done` frame (§5.3 visible plan). */
  plan: PlanStep[]
  /** The turn's certainty claims, resolved on the `done` frame (§5.3 uncertainty). */
  claims: Claim[]
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
  const [plan, setPlan] = useState<PlanStep[]>([])
  const [claims, setClaims] = useState<Claim[]>([])
  const socketRef = useRef<WebSocket | null>(null)

  // Reset the buffer when we (re)target a message. Adjusting state during render —
  // rather than in an effect — avoids a wasted render pass with stale text.
  const [trackedId, setTrackedId] = useState(assistantMessageId)
  if (trackedId !== assistantMessageId) {
    setTrackedId(assistantMessageId)
    setText('')
    setIsDone(false)
    setError(null)
    setPlan([])
    setClaims([])
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
          // The plan/claims (may be empty) resolve when the turn completes; the prose
          // already streamed token-by-token (block-stripped server-side).
          if (chunk.plan) setPlan(chunk.plan)
          if (chunk.claims) setClaims(chunk.claims)
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

  return { text, isDone, error, plan, claims }
}
