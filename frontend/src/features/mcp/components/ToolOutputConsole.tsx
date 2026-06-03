import { useEffect, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useToolRunStream } from '../hooks/useToolRunStream'
import { useKillToolRun, useTimeoutDecision } from '../api'

interface ToolOutputConsoleProps {
  /** The engagement this run belongs to — needed to drive the kill / timeout-decision mutations. */
  engagementId: string
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
 *  - awaiting_decision: shows the timeout prompt with Kill / Extend / Wait.
 *  - killed: shows the Killed badge.
 *  - done: shows the Completed/Failed badge.
 *
 * Stop button: visible while the run is running, queued, or awaiting_decision
 *   (i.e. while !isDone). Calls useKillToolRun to stop the run.
 */
export function ToolOutputConsole({ engagementId, toolRunId }: ToolOutputConsoleProps) {
  const { lines, isDone, exitCode, queued, queuePosition, queueReason, awaitingTimeout, killed } =
    useToolRunStream(toolRunId)
  const endRef = useRef<HTMLDivElement | null>(null)

  const killMutation = useKillToolRun(engagementId)
  const timeoutDecisionMutation = useTimeoutDecision(engagementId)

  // Auto-scroll to the latest line as output arrives.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [lines])

  if (!toolRunId) {
    return (
      <p className="text-sm text-muted-foreground">Run a tool to see its output here.</p>
    )
  }

  // The Stop button is visible while the run is still live (running, queued,
  // or awaiting_decision). Once isDone is true (killed or completed/failed) it
  // is hidden. Killing an already-terminal run is idempotent on the backend,
  // but showing the button after termination would be confusing.
  const showStopButton = !isDone
  const isKillingNow = killMutation.isPending

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
          {showStopButton && (
            <Button
              variant="destructive"
              size="xs"
              disabled={isKillingNow}
              onClick={() => killMutation.mutate(toolRunId)}
              data-testid="stop-button"
            >
              {isKillingNow ? 'Stopping…' : 'Stop'}
            </Button>
          )}
        </div>
        {reasonText && (
          <p className="text-xs text-muted-foreground" data-testid="queue-reason">
            {reasonText}
          </p>
        )}
      </div>
    )
  }

  // --- Awaiting-decision state: the run timed out and released its slot.
  //     Render the timeout prompt. The Stop button is also available (acts as
  //     a quick kill shortcut alongside the Kill button in the prompt). ---
  if (awaitingTimeout) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Output</span>
          <Badge variant="outline">Awaiting decision</Badge>
          {showStopButton && (
            <Button
              variant="destructive"
              size="xs"
              disabled={isKillingNow || timeoutDecisionMutation.isPending}
              onClick={() => killMutation.mutate(toolRunId)}
              data-testid="stop-button"
            >
              {isKillingNow ? 'Stopping…' : 'Stop'}
            </Button>
          )}
        </div>

        {/* Timeout prompt — no kill countdown; the prompt stays open until answered. */}
        <div
          className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-3 flex flex-col gap-2"
          data-testid="timeout-prompt"
        >
          <p className="text-sm font-medium text-foreground">
            Timed out — what do you want to do?
          </p>
          <p className="text-xs text-muted-foreground">
            The run's concurrency slot has been released — the queue can advance while you
            decide. This prompt stays open until you answer.
          </p>
          <div className="flex items-center gap-2 pt-1">
            <Button
              variant="destructive"
              size="sm"
              disabled={timeoutDecisionMutation.isPending || isKillingNow}
              onClick={() =>
                timeoutDecisionMutation.mutate({
                  toolRunId,
                  decision: 'kill',
                  extend_seconds: 30,
                })
              }
              data-testid="timeout-kill-button"
            >
              Kill
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={timeoutDecisionMutation.isPending || isKillingNow}
              onClick={() =>
                timeoutDecisionMutation.mutate({
                  toolRunId,
                  decision: 'extend',
                  extend_seconds: 30,
                })
              }
              data-testid="timeout-extend-button"
            >
              Extend (+30s)
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={timeoutDecisionMutation.isPending || isKillingNow}
              onClick={() =>
                timeoutDecisionMutation.mutate({
                  toolRunId,
                  decision: 'wait',
                  extend_seconds: 30,
                })
              }
              data-testid="timeout-wait-button"
            >
              Wait
            </Button>
          </div>
        </div>

        {/* Still show the output lines accumulated before the timeout. */}
        {lines.length > 0 && (
          <pre
            className="max-h-64 overflow-auto rounded-md border bg-muted p-3 text-xs leading-relaxed"
            data-testid="tool-output"
          >
            {lines.map((line, i) => (
              <div key={i} className={line.stream === 'stderr' ? 'text-red-400' : undefined}>
                {line.text}
              </div>
            ))}
            <div ref={endRef} />
          </pre>
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
          <>
            <span role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span
                aria-hidden
                className="h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
              />
              Running…
            </span>
            {showStopButton && (
              <Button
                variant="destructive"
                size="xs"
                disabled={isKillingNow}
                onClick={() => killMutation.mutate(toolRunId)}
                data-testid="stop-button"
              >
                {isKillingNow ? 'Stopping…' : 'Stop'}
              </Button>
            )}
          </>
        ) : killed ? (
          <Badge variant="destructive" data-testid="killed-badge">
            Killed
          </Badge>
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
