import { useEffect, useRef, useState } from 'react'

/**
 * A single line of streamed tool output, tagged with the stream it came from so
 * the console can highlight stderr differently. The slice spec sketches the
 * return buffer as `string[]`; we carry the stream tag alongside the text so the
 * console (FE task 5) can colour stderr without re-parsing.
 */
export interface ToolRunLine {
  stream: 'stdout' | 'stderr'
  text: string
}

/**
 * Message shape pushed by the WebSocket endpoint (`WebSocketOutputChunk`). The
 * type is not in the generated OpenAPI client because WebSocket endpoints are
 * not part of the OpenAPI document, so it is declared here to match the backend
 * contract: type stdout|stderr carry `data`; done carries `exit_code` /
 * `finished_at`; error carries `message`; queued carries `queue_position` and
 * optionally `reason` (the first queued message); started carries no extra fields.
 *
 * Slice 06 additions:
 *   timeout — the run hit its deadline, released its concurrency slot back to
 *     the queue, and is parked awaiting a kill/extend/wait decision. `message`
 *     carries a human-readable note that the slot was released. No auto-kill
 *     grace countdown is ever sent — the prompt stays open until the human
 *     answers via POST /tool-runs/{id}/timeout-decision.
 *   killed — the run was stopped (per-tool kill or engagement pause); `message`
 *     carries the cause. On extend/wait, the stream resumes from a fresh
 *     `started` chunk once the slot is re-acquired.
 */
interface WebSocketOutputChunk {
  type: 'queued' | 'started' | 'stdout' | 'stderr' | 'timeout' | 'killed' | 'done' | 'error'
  data?: string
  exit_code?: number | null
  finished_at?: string | null
  message?: string
  queue_position?: number
  reason?: 'slot_full' | 'target_locked'
}

export interface ToolRunStream {
  lines: ToolRunLine[]
  isDone: boolean
  exitCode: number | null
  queued: boolean
  queuePosition: number | null
  queueReason: 'slot_full' | 'target_locked' | null
  /**
   * True while the run is parked in the `awaiting_decision` state: it hit its
   * timeout, released its concurrency slot back to the queue, and is waiting for
   * a kill/extend/wait decision via POST /tool-runs/{id}/timeout-decision.
   * No kill countdown is ever exposed — the prompt stays open indefinitely.
   * Cleared when a `started` chunk arrives (the run re-acquired a slot on
   * extend/wait) or when a `killed` chunk arrives.
   */
  awaitingTimeout: boolean
  /**
   * True once a `killed` chunk is received (per-tool stop or engagement pause).
   * Always accompanied by `isDone=true`. The cause message is appended as a
   * stderr line in `lines`.
   */
  killed: boolean
}

/** Build the ws(s):// URL for a tool run, mirroring the same-origin API client. */
function buildWsUrl(toolRunId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || window.location.origin
  const wsBase = base.replace(/^http/, 'ws')
  return `${wsBase}/ws/tool-runs/${toolRunId}`
}

/**
 * Subscribe to a tool run's live output over WebSocket.
 *
 * Opens a socket when `toolRunId` is non-null, appends each stdout/stderr chunk
 * to a local buffer, and resolves `isDone` / `exitCode` on the `done` message.
 * The socket is closed on `done`, on `error`, and on unmount or when
 * `toolRunId` changes.
 */
export function useToolRunStream(toolRunId: string | null): ToolRunStream {
  const [lines, setLines] = useState<ToolRunLine[]>([])
  const [isDone, setIsDone] = useState(false)
  const [exitCode, setExitCode] = useState<number | null>(null)
  const [queued, setQueued] = useState(false)
  const [queuePosition, setQueuePosition] = useState<number | null>(null)
  const [queueReason, setQueueReason] = useState<'slot_full' | 'target_locked' | null>(null)
  const [awaitingTimeout, setAwaitingTimeout] = useState(false)
  const [killed, setKilled] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)

  // Reset the buffer when we (re)target a run. Adjusting state during render —
  // rather than in an effect — avoids a wasted render pass with stale lines and
  // the "setState synchronously within an effect" lint rule.
  const [trackedId, setTrackedId] = useState(toolRunId)
  if (trackedId !== toolRunId) {
    setTrackedId(toolRunId)
    setLines([])
    setIsDone(false)
    setExitCode(null)
    setQueued(false)
    setQueuePosition(null)
    setQueueReason(null)
    setAwaitingTimeout(false)
    setKilled(false)
  }

  useEffect(() => {
    if (!toolRunId) return

    const socket = new WebSocket(buildWsUrl(toolRunId))
    socketRef.current = socket

    socket.onmessage = (event: MessageEvent) => {
      let chunk: WebSocketOutputChunk
      try {
        chunk = JSON.parse(event.data as string) as WebSocketOutputChunk
      } catch {
        return
      }

      switch (chunk.type) {
        case 'queued':
          setQueued(true)
          setQueuePosition(chunk.queue_position ?? null)
          if (chunk.reason !== undefined) {
            setQueueReason(chunk.reason)
          }
          break
        case 'started':
          // Clear queue state as usual.
          setQueued(false)
          setQueuePosition(null)
          setQueueReason(null)
          // If a `started` chunk arrives while awaitingTimeout is set, the run
          // re-acquired a slot (extend/wait decision). Clear the timeout prompt
          // and resume normal streaming.
          setAwaitingTimeout(false)
          break
        case 'stdout':
        case 'stderr':
          setLines((prev) => [...prev, { stream: chunk.type as 'stdout' | 'stderr', text: chunk.data ?? '' }])
          break
        case 'timeout':
          // The run hit its deadline and released its concurrency slot back to
          // the queue. No countdown — the prompt stays open indefinitely until
          // the human answers via POST /tool-runs/{id}/timeout-decision.
          setAwaitingTimeout(true)
          if (chunk.message) {
            setLines((prev) => [...prev, { stream: 'stderr', text: chunk.message as string }])
          }
          break
        case 'killed':
          // The run was stopped (per-tool kill or engagement pause). Append the
          // cause message, mark the run done, and close the socket.
          if (chunk.message) {
            setLines((prev) => [...prev, { stream: 'stderr', text: chunk.message as string }])
          }
          setKilled(true)
          setAwaitingTimeout(false)
          setIsDone(true)
          socket.close()
          break
        case 'done':
          setExitCode(chunk.exit_code ?? null)
          setIsDone(true)
          socket.close()
          break
        case 'error':
          if (chunk.message) {
            setLines((prev) => [...prev, { stream: 'stderr', text: chunk.message as string }])
          }
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
  }, [toolRunId])

  return { lines, isDone, exitCode, queued, queuePosition, queueReason, awaitingTimeout, killed }
}
