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
 * `finished_at`; error carries `message`.
 */
interface WebSocketOutputChunk {
  type: 'stdout' | 'stderr' | 'done' | 'error'
  data?: string
  exit_code?: number | null
  finished_at?: string | null
  message?: string
}

export interface ToolRunStream {
  lines: ToolRunLine[]
  isDone: boolean
  exitCode: number | null
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
        case 'stdout':
        case 'stderr':
          setLines((prev) => [...prev, { stream: chunk.type as 'stdout' | 'stderr', text: chunk.data ?? '' }])
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

  return { lines, isDone, exitCode }
}
