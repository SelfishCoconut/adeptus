import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { useEngagement, useEngagementPause } from '@/features/engagements/api'
import { ToolRunnerForm } from './ToolRunnerForm'
import { ToolOutputConsole } from './ToolOutputConsole'
import { ToolRunHistory } from './ToolRunHistory'
import { ToolQueueStrip } from './ToolQueueStrip'

type Tab = 'runner' | 'history'

interface ToolRunnerPanelProps {
  engagementId: string
}

const TAB_BUTTON_BASE =
  'border-b-2 px-3 py-1.5 text-sm font-medium transition-colors -mb-px'

/**
 * Bottom-pane tool runner: a Runner tab (form + live output console) and a
 * History tab (past runs). Selecting a historical run switches back to the
 * Runner tab and replays it in the console; the active run id lives here so it
 * survives tab switches.
 *
 * The Pause / Resume toggle is placed in the panel header for cohesion with the
 * tool-runner controls (per the slice-06 spec: "keep it in the panel header for
 * cohesion"). It reads the `paused` flag from the existing engagement detail
 * query — no separate fetch is added. The `paused` flag is also threaded into
 * `ToolRunnerForm` to disable the Run button while the engagement is paused.
 */
export function ToolRunnerPanel({ engagementId }: ToolRunnerPanelProps) {
  const [tab, setTab] = useState<Tab>('runner')
  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  const engagementQuery = useEngagement(engagementId)
  const paused = engagementQuery.data?.paused ?? false

  const pauseMutation = useEngagementPause(engagementId)

  function handleSelectRun(toolRunId: string) {
    setActiveRunId(toolRunId)
    setTab('runner')
  }

  function handlePauseToggle() {
    pauseMutation.mutate({ paused: !paused })
  }

  return (
    <div className="flex h-full flex-col gap-3">
      {/* Paused banner — persistent while engagement is paused */}
      {paused && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-md border border-yellow-400/60 bg-yellow-50 px-4 py-2 text-sm font-medium text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300"
        >
          Engagement paused — tool runs are halted
        </div>
      )}

      {/* Tab bar + Pause / Resume button in the same row */}
      <div className="flex items-center justify-between border-b">
        <div role="tablist" className="flex gap-1">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'runner'}
            onClick={() => setTab('runner')}
            className={`${TAB_BUTTON_BASE} ${
              tab === 'runner' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground'
            }`}
          >
            Runner
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'history'}
            onClick={() => setTab('history')}
            className={`${TAB_BUTTON_BASE} ${
              tab === 'history' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground'
            }`}
          >
            History
          </button>
        </div>

        <Button
          type="button"
          variant={paused ? 'default' : 'outline'}
          size="sm"
          className="mb-1"
          onClick={handlePauseToggle}
          disabled={pauseMutation.isPending}
        >
          {paused ? 'Resume' : 'Pause'}
        </Button>
      </div>

      {tab === 'runner' ? (
        <div role="tabpanel" className="flex flex-col gap-4">
          <ToolQueueStrip engagementId={engagementId} />
          <ToolRunnerForm
            engagementId={engagementId}
            onRunStarted={setActiveRunId}
            paused={paused}
          />
          <ToolOutputConsole engagementId={engagementId} toolRunId={activeRunId} />
        </div>
      ) : (
        <div role="tabpanel">
          <ToolRunHistory engagementId={engagementId} onSelectRun={handleSelectRun} />
        </div>
      )}
    </div>
  )
}
