import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { useToolQueue } from '../api'

interface ToolQueueStripProps {
  /** The engagement whose heavy-tool pool is shown. */
  engagementId: string
}

/**
 * Compact strip that shows "N running / M queued" for the engagement's
 * heavy-tool pool. Polls via `useToolQueue` every 2 s (Decision 7: poll,
 * no second WebSocket).
 *
 * Empty-state rule: renders nothing when running_count === 0 AND
 * queued_count === 0 so the strip does not appear when the pool is idle.
 *
 * When there is activity, the user can click the strip to expand a FIFO list
 * of queued runs showing tool name and 1-based position.
 */
export function ToolQueueStrip({ engagementId }: ToolQueueStripProps) {
  const { data } = useToolQueue(engagementId)
  const [expanded, setExpanded] = useState(false)

  // Nothing to show while loading or when the pool is idle.
  if (!data || (data.running_count === 0 && data.queued_count === 0)) {
    return null
  }

  const label = `${data.running_count} running / ${data.queued_count} queued`

  return (
    <div className="flex flex-col gap-1" data-testid="tool-queue-strip">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-fit"
        aria-expanded={expanded}
        aria-label="Tool queue status"
      >
        <Badge variant="outline" className="gap-1" data-testid="queue-counts">
          {label}
        </Badge>
        <span aria-hidden>{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && data.queued.length > 0 && (
        <ul
          className="ml-1 flex flex-col gap-0.5 border-l border-border pl-3"
          data-testid="queue-list"
        >
          {data.queued.map((run) => (
            <li
              key={run.tool_run_id}
              className="flex items-center gap-2 text-xs text-muted-foreground"
              data-testid={`queue-item-${run.position}`}
            >
              <span className="tabular-nums font-medium text-foreground">
                #{run.position}
              </span>
              <span>{run.tool_name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
