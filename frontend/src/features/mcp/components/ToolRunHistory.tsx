import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useListToolRuns } from '../api'
import type { ToolRunResult } from '@/shared/api'

interface ToolRunHistoryProps {
  engagementId: string
  /** Called with a run id when a history row is clicked (activates replay). */
  onSelectRun: (toolRunId: string) => void
}

const STATUS_VARIANT: Record<ToolRunResult['status'], 'secondary' | 'default' | 'destructive'> = {
  queued: 'default',
  completed: 'secondary',
  running: 'default',
  failed: 'destructive',
  timed_out: 'destructive',
}

function formatStarted(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export function ToolRunHistory({ engagementId, onSelectRun }: ToolRunHistoryProps) {
  const query = useListToolRuns(engagementId)

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading run history…</p>
  }

  if (query.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Failed to load run history.
      </p>
    )
  }

  const runs = query.data?.pages.flatMap((page) => page.items) ?? []

  if (runs.length === 0) {
    return <p className="text-sm text-muted-foreground">No tool runs yet.</p>
  }

  return (
    <div className="flex flex-col gap-2">
      <ul className="flex flex-col gap-1">
        {runs.map((run) => (
          <li key={run.tool_run_id}>
            <button
              type="button"
              onClick={() => onSelectRun(run.tool_run_id)}
              className="flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-accent"
            >
              <span className="font-medium">{run.tool_name}</span>
              {run.preset_name && (
                <span className="text-xs text-muted-foreground">{run.preset_name}</span>
              )}
              <span className="text-xs text-muted-foreground">{formatStarted(run.started_at)}</span>
              <Badge variant={STATUS_VARIANT[run.status]} className="ml-auto">
                {run.status}
              </Badge>
              <span className="text-xs text-muted-foreground">exit {run.exit_code ?? '—'}</span>
            </button>
          </li>
        ))}
      </ul>

      {query.hasNextPage && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </div>
  )
}
