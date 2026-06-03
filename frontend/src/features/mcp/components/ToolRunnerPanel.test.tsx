import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolRunnerPanel } from './ToolRunnerPanel'
import { useEngagement, useEngagementPause } from '@/features/engagements/api'

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock('@/features/engagements/api', () => ({
  useEngagement: vi.fn(),
  useEngagementPause: vi.fn(),
}))

const mockedUseEngagement = vi.mocked(useEngagement)
const mockedUseEngagementPause = vi.mocked(useEngagementPause)

// Stub the child components so the panel's wiring (tabs + active run id +
// pause toggle) is tested in isolation from the form / console / history internals.
vi.mock('./ToolQueueStrip', () => ({
  ToolQueueStrip: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="tool-queue-strip-mock">strip:{engagementId}</div>
  ),
}))

vi.mock('./ToolRunnerForm', () => ({
  ToolRunnerForm: ({
    engagementId,
    onRunStarted,
    paused,
  }: {
    engagementId: string
    onRunStarted: (id: string) => void
    paused?: boolean
  }) => (
    <div>
      <span>form:{engagementId}</span>
      {paused && <span data-testid="form-paused-flag">form-paused</span>}
      <button type="button" onClick={() => onRunStarted('live-run')}>
        start run
      </button>
      <button type="button" disabled={paused}>
        Run
      </button>
    </div>
  ),
}))

vi.mock('./ToolOutputConsole', () => ({
  ToolOutputConsole: ({
    toolRunId,
  }: {
    engagementId: string
    toolRunId: string | null
  }) => <div>console:{toolRunId ?? 'none'}</div>,
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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

const ENGAGEMENT_DETAIL_NOT_PAUSED = {
  id: ENGAGEMENT_ID,
  name: 'Alpha',
  status: 'active' as const,
  scope: '10.0.0.0/8',
  client_info: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  member_role: 'owner' as const,
  privacy_mode: 'local_only' as const,
  concurrency_slot_limit: 2,
  paused: false,
}

const ENGAGEMENT_DETAIL_PAUSED = { ...ENGAGEMENT_DETAIL_NOT_PAUSED, paused: true }

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function engagementQueryResult(paused: boolean) {
  const detail = paused ? ENGAGEMENT_DETAIL_PAUSED : ENGAGEMENT_DETAIL_NOT_PAUSED
  return {
    data: detail,
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useEngagement>
}

function pauseMutationResult(overrides: { mutate?: (...args: unknown[]) => void; isPending?: boolean } = {}) {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    mutateAsync: vi.fn(),
    isPending: overrides.isPending ?? false,
    isError: false,
    isIdle: true,
    isSuccess: false,
    error: null,
    data: undefined,
    reset: vi.fn(),
    status: 'idle' as const,
    variables: undefined,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    isPaused: false,
    submittedAt: 0,
  } as unknown as ReturnType<typeof useEngagementPause>
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ToolRunnerPanel', () => {
  // Default: not paused

  it('shows the Runner tab with form and console by default', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByText(`form:${ENGAGEMENT_ID}`)).toBeInTheDocument()
    expect(screen.getByText('console:none')).toBeInTheDocument()
    expect(screen.queryByText('history')).not.toBeInTheDocument()
  })

  it('renders ToolQueueStrip in the Runner tab with the engagement id', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByTestId('tool-queue-strip-mock')).toBeInTheDocument()
    expect(screen.getByTestId('tool-queue-strip-mock')).toHaveTextContent(
      `strip:${ENGAGEMENT_ID}`,
    )
  })

  it('does not render ToolQueueStrip when on the History tab', async () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(screen.queryByTestId('tool-queue-strip-mock')).not.toBeInTheDocument()
  })

  it('switches to the History tab', async () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(screen.getByText('history')).toBeInTheDocument()
    expect(screen.queryByText(`form:${ENGAGEMENT_ID}`)).not.toBeInTheDocument()
  })

  it('passes the active run id to the console when a run starts', async () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'start run' }))
    expect(screen.getByText('console:live-run')).toBeInTheDocument()
  })

  it('selecting a historical run switches to Runner and replays it', async () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('tab', { name: 'History' }))
    await user.click(screen.getByRole('button', { name: 'select historical' }))

    // Back on the Runner tab, console now shows the historical run.
    expect(screen.getByText('console:hist-run')).toBeInTheDocument()
    expect(screen.getByText(`form:${ENGAGEMENT_ID}`)).toBeInTheDocument()
  })

  it('preserves the active run id across a tab round-trip', async () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'start run' }))
    expect(screen.getByText('console:live-run')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'History' }))
    await user.click(screen.getByRole('tab', { name: 'Runner' }))

    expect(screen.getByText('console:live-run')).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Pause / Resume toggle
  // ---------------------------------------------------------------------------

  it('shows a Pause button when the engagement is not paused', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument()
  })

  it('fires the pause mutation with paused:true when Pause is clicked', async () => {
    const mutate = vi.fn()
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult({ mutate }))

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'Pause' }))
    expect(mutate).toHaveBeenCalledWith({ paused: true })
  })

  it('shows a Resume button when the engagement is paused', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(true))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
  })

  it('fires the pause mutation with paused:false when Resume is clicked', async () => {
    const mutate = vi.fn()
    mockedUseEngagement.mockReturnValue(engagementQueryResult(true))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult({ mutate }))

    const user = userEvent.setup()
    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: 'Resume' }))
    expect(mutate).toHaveBeenCalledWith({ paused: false })
  })

  // ---------------------------------------------------------------------------
  // Paused banner + Run button disabled
  // ---------------------------------------------------------------------------

  it('renders the paused banner when engagement is paused', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(true))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('status')).toHaveTextContent(
      'Engagement paused — tool runs are halted',
    )
  })

  it('does not render the paused banner when engagement is not paused', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('disables the Run button when paused by passing paused prop to ToolRunnerForm', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(true))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    // The mock ToolRunnerForm renders a disabled Run button when paused=true.
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    // Also confirm the form received the paused flag.
    expect(screen.getByTestId('form-paused-flag')).toBeInTheDocument()
  })

  it('enables the Run button when not paused', () => {
    mockedUseEngagement.mockReturnValue(engagementQueryResult(false))
    mockedUseEngagementPause.mockReturnValue(pauseMutationResult())

    render(<ToolRunnerPanel engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('button', { name: 'Run' })).not.toBeDisabled()
    expect(screen.queryByTestId('form-paused-flag')).not.toBeInTheDocument()
  })
})
