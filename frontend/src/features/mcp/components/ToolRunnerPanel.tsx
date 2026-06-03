import { useState } from 'react'
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
 */
export function ToolRunnerPanel({ engagementId }: ToolRunnerPanelProps) {
  const [tab, setTab] = useState<Tab>('runner')
  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  function handleSelectRun(toolRunId: string) {
    setActiveRunId(toolRunId)
    setTab('runner')
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div role="tablist" className="flex gap-1 border-b">
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

      {tab === 'runner' ? (
        <div role="tabpanel" className="flex flex-col gap-4">
          <ToolQueueStrip engagementId={engagementId} />
          <ToolRunnerForm engagementId={engagementId} onRunStarted={setActiveRunId} />
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
