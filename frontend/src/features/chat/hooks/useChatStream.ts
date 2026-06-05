import { useEffect, useRef, useState } from 'react'
import type { ApprovalRequest, Claim, PlanStep } from '@/shared/api'
import type { AutonomousAction } from '@/features/approvals/api'

/**
 * A single frame pushed by the chat WebSocket endpoint (`WebSocketChatChunk`). Not
 * part of the OpenAPI document (WebSocket endpoints never are), so it is declared here
 * to match the backend contract:
 *   token — `data` carries an incremental slice of assistant text (append to buffer).
 *   proposed_action — a command the AI proposed this turn (Slice 16, §5.2): `approval_request`
 *           for a gated command (render the approval card) OR `autonomous_action` for a command
 *           running now (render the "running automatically" card).
 *   done  — the stream finished; the assistant message is persisted complete. `plan` and
 *           `claims` (Slice 13) carry the turn's parsed plan + claims; `approval_requests`
 *           (Slice 16) repeats the full gated list for idempotent reconciliation.
 *   error — `message` carries a stable, non-leaky reason (e.g. the model being offline).
 */
interface WebSocketChatChunk {
  type: 'token' | 'proposed_action' | 'done' | 'error'
  data?: string
  message?: string
  plan?: PlanStep[]
  claims?: Claim[]
  approval_request?: ApprovalRequest
  autonomous_action?: AutonomousAction
  approval_requests?: ApprovalRequest[]
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
  /** Gated approval requests this turn created (§5.2); render each as an inline card. */
  approvalRequests: ApprovalRequest[]
  /** Autonomous commands running now (§5.2); render each as a "running automatically" card. */
  autonomousActions: AutonomousAction[]
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
  const [approvalRequests, setApprovalRequests] = useState<ApprovalRequest[]>([])
  const [autonomousActions, setAutonomousActions] = useState<AutonomousAction[]>([])
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
    setApprovalRequests([])
    setAutonomousActions([])
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
        case 'proposed_action':
          // A command the AI proposed mid-turn: surface the card the moment it lands.
          if (chunk.approval_request) {
            const gated = chunk.approval_request
            setApprovalRequests((prev) =>
              prev.some((r) => r.id === gated.id) ? prev : [...prev, gated],
            )
          }
          if (chunk.autonomous_action) {
            const auto = chunk.autonomous_action
            setAutonomousActions((prev) =>
              prev.some((a) => a.tool_run_id === auto.tool_run_id) ? prev : [...prev, auto],
            )
          }
          break
        case 'done':
          // The plan/claims (may be empty) resolve when the turn completes; the prose
          // already streamed token-by-token (block-stripped server-side). The done frame
          // repeats the full gated list for idempotent reconciliation (Slice 16).
          if (chunk.plan) setPlan(chunk.plan)
          if (chunk.claims) setClaims(chunk.claims)
          if (chunk.approval_requests) setApprovalRequests(chunk.approval_requests)
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

  return { text, isDone, error, plan, claims, approvalRequests, autonomousActions }
}
