import { useEffect, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { useToolRunStream } from '../hooks/useToolRunStream'

interface ToolOutputConsoleProps {
  /** The run whose output to display, or null when no run is active. */
  toolRunId: string | null
}

/** Map the machine reason code to human-readable copy. */
function queueReasonText(reason: 'slot_full' | 'target_locked' | null): string {
  if (reason === 'slot_full') return 'waiting for a free slot'
  if (reason === 'target_locked') return 'waiting on the target host lock'
  return ''
}

/**
 * Streams a tool run's output into a scrollable console. Drives off
 * useToolRunStream, which serves both live runs and — via the WebSocket
 * completed-run fallback — historical runs selected from the history list.
 *
 * States:
 *  - queued: shows a "Queued — position N" badge with a human reason instead
 *    of the running spinner or output view.
 *  - running (not queued, not done): shows the running spinner and output.
 *  - done: shows the Completed/Failed badge.
 */
export function ToolOutputConsole({ toolRunId }: ToolOutputConsoleProps) {
  const { lines, isDone, exitCode, queued, queuePosition, queueReason } =
    useToolRunStream(toolRunId)
  const endRef = useRef<HTMLDivElement | null>(null)

  // Auto-scroll to the latest line as output arrives.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [lines])

  if (!toolRunId) {
    return (
      <p className="text-sm text-muted-foreground">Run a tool to see its output here.</p>
    )
  }

  // --- Queued state: show a badge instead of the spinner / output ---
  if (queued) {
    const positionLabel =
      queuePosition !== null ? `Queued — position ${queuePosition}` : 'Queued'
    const reasonText = queueReasonText(queueReason)

    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Output</span>
          <Badge
            role="status"
            aria-label={positionLabel}
            variant="outline"
            className="gap-1"
          >
            {positionLabel}
          </Badge>
        </div>
        {reasonText && (
          <p className="text-xs text-muted-foreground" data-testid="queue-reason">
            {reasonText}
          </p>
        )}
      </div>
    )
  }

  // --- Running / done state ---
  const isRunning = !isDone

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Output</span>
        {isRunning ? (
          <span role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              aria-hidden
              className="h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
            />
            Running…
          </span>
        ) : (
          <Badge variant={exitCode === 0 ? 'secondary' : 'destructive'}>
            {exitCode === 0 ? 'Completed' : 'Failed'} · exit {exitCode ?? '—'}
          </Badge>
        )}
      </div>

      <pre
        className="max-h-64 overflow-auto rounded-md border bg-muted p-3 text-xs leading-relaxed"
        data-testid="tool-output"
      >
        {lines.length === 0 ? (
          <span className="text-muted-foreground">(no output yet)</span>
        ) : (
          lines.map((line, i) => (
            <div key={i} className={line.stream === 'stderr' ? 'text-red-400' : undefined}>
              {line.text}
            </div>
          ))
        )}
        <div ref={endRef} />
      </pre>
    </div>
  )
}
