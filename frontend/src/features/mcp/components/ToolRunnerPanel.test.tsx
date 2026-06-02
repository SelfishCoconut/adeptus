import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolRunnerPanel } from './ToolRunnerPanel'

// Stub the children so the panel's wiring (tabs + active run id) is tested in
// isolation from the form / console / history internals.
vi.mock('./ToolRunnerForm', () => ({
  ToolRunnerForm: ({
    engagementId,
    onRunStarted,
  }: {
    engagementId: string
    onRunStarted: (id: string) => void
  }) => (
    <div>
      <span>form:{engagementId}</span>
      <button type="button" onClick={() => onRunStarted('live-run')}>
        start run
      </button>
    </div>
  ),
}))

vi.mock('./ToolOutputConsole', () => ({
  ToolOutputConsole: ({ toolRunId }: { toolRunId: string | null }) => (
    <div>console:{toolRunId ?? 'none'}</div>
  ),
}))

vi.mock('./ToolRunHistory', () => ({
  ToolRunHistory: ({ onSelectRun }: { onSelectRun: (id: string) => void }) => (
    <div>
      <span>history</span>
      <button type="button" onClick={() => onSelectRun('hist-run')}>
        select historical
      </button>
    </div>
  ),
}))

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

describe('ToolRunnerPanel', () => {
  it('shows the Runner tab with form and console by default', () => {
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByText(`form:${ENGAGEMENT_ID}`)).toBeInTheDocument()
    expect(screen.getByText('console:none')).toBeInTheDocument()
    expect(screen.queryByText('history')).not.toBeInTheDocument()
  })

  it('switches to the History tab', async () => {
    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(screen.getByText('history')).toBeInTheDocument()
    expect(screen.queryByText(`form:${ENGAGEMENT_ID}`)).not.toBeInTheDocument()
  })

  it('passes the active run id to the console when a run starts', async () => {
    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'start run' }))
    expect(screen.getByText('console:live-run')).toBeInTheDocument()
  })

  it('selecting a historical run switches to Runner and replays it', async () => {
    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('tab', { name: 'History' }))
    await user.click(screen.getByRole('button', { name: 'select historical' }))

    // Back on the Runner tab, console now shows the historical run.
    expect(screen.getByText('console:hist-run')).toBeInTheDocument()
    expect(screen.getByText(`form:${ENGAGEMENT_ID}`)).toBeInTheDocument()
  })

  it('preserves the active run id across a tab round-trip', async () => {
    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'start run' }))
    expect(screen.getByText('console:live-run')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'History' }))
    await user.click(screen.getByRole('tab', { name: 'Runner' }))

    expect(screen.getByText('console:live-run')).toBeInTheDocument()
  })
})
