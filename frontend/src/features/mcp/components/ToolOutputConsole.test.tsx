import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolOutputConsole } from './ToolOutputConsole'
import { useToolRunStream, type ToolRunStream } from '../hooks/useToolRunStream'
import { useKillToolRun, useTimeoutDecision } from '../api'

vi.mock('../hooks/useToolRunStream', () => ({
  useToolRunStream: vi.fn(),
}))

// Mock the mutation hooks so we can inspect calls without a real QueryClient.
vi.mock('../api', () => ({
  useKillToolRun: vi.fn(),
  useTimeoutDecision: vi.fn(),
}))

const mockedUseToolRunStream = vi.mocked(useToolRunStream)
const mockedUseKillToolRun = vi.mocked(useKillToolRun)
const mockedUseTimeoutDecision = vi.mocked(useTimeoutDecision)

// Default no-op mutation objects returned by the mocked hooks.
const killMutateFn = vi.fn()
const timeoutDecisionMutateFn = vi.fn()

function makeKillMutation(overrides: Record<string, unknown> = {}) {
  return {
    mutate: killMutateFn,
    isPending: false,
    ...overrides,
  }
}

function makeTimeoutDecisionMutation(overrides: Record<string, unknown> = {}) {
  return {
    mutate: timeoutDecisionMutateFn,
    isPending: false,
    ...overrides,
  }
}

function streamResult(overrides: Partial<ToolRunStream>): ToolRunStream {
  return {
    lines: [],
    isDone: false,
    exitCode: null,
    queued: false,
    queuePosition: null,
    queueReason: null,
    awaitingTimeout: false,
    killed: false,
    ...overrides,
  }
}

const TOOL_RUN_ID = '00000000-0000-0000-0000-000000000001'
const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000002'

beforeEach(() => {
  mockedUseToolRunStream.mockReset()
  killMutateFn.mockReset()
  timeoutDecisionMutateFn.mockReset()
  // Return default no-op mutations unless a test overrides them.
  mockedUseKillToolRun.mockReturnValue(
    makeKillMutation() as unknown as ReturnType<typeof useKillToolRun>,
  )
  mockedUseTimeoutDecision.mockReturnValue(
    makeTimeoutDecisionMutation() as unknown as ReturnType<typeof useTimeoutDecision>,
  )
  // jsdom does not implement scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn()
})

describe('ToolOutputConsole', () => {
  it('shows a placeholder when no run is active', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({}))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={null} />)
    expect(screen.getByText(/run a tool to see its output/i)).toBeInTheDocument()
  })

  it('renders output lines in order', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({
        lines: [
          { stream: 'stdout', text: 'first line' },
          { stream: 'stdout', text: 'second line' },
        ],
      }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    expect(screen.getByText('first line')).toBeInTheDocument()
    expect(screen.getByText('second line')).toBeInTheDocument()
  })

  it('shows a running spinner while the run is in progress', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: false }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByRole('status')).toHaveTextContent(/running/i)
  })

  it('shows a Completed badge with exit 0 on success', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: true, exitCode: 0 }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByText(/completed · exit 0/i)).toBeInTheDocument()
  })

  it('shows a Failed badge for a non-zero exit code', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: true, exitCode: 2 }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByText(/failed · exit 2/i)).toBeInTheDocument()
  })

  it('highlights stderr lines with the red text class', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({
        lines: [
          { stream: 'stdout', text: 'ok line' },
          { stream: 'stderr', text: 'error line' },
        ],
      }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    expect(screen.getByText('ok line')).not.toHaveClass('text-red-400')
    expect(screen.getByText('error line')).toHaveClass('text-red-400')
  })

  it('auto-scrolls to the latest line when output changes', () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ lines: [{ stream: 'stdout', text: 'line' }] }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(scrollIntoView).toHaveBeenCalled()
  })

  // --- Queued state tests ---

  it('shows a "Queued — position N" badge when the run is queued', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 1, queueReason: 'slot_full' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    expect(screen.getByRole('status')).toHaveTextContent('Queued — position 1')
    // Running spinner and output pane must not be shown
    expect(screen.queryByText(/running…/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('tool-output')).not.toBeInTheDocument()
  })

  it('shows "Queued" without a position number when queuePosition is null', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: null, queueReason: 'slot_full' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByRole('status')).toHaveTextContent('Queued')
    expect(screen.getByRole('status')).not.toHaveTextContent('position')
  })

  it('shows "waiting for a free slot" reason for slot_full', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 2, queueReason: 'slot_full' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('queue-reason')).toHaveTextContent('waiting for a free slot')
  })

  it('shows "waiting on the target host lock" reason for target_locked', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 1, queueReason: 'target_locked' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('queue-reason')).toHaveTextContent('waiting on the target host lock')
  })

  it('transitions from queued badge to streaming output when started', () => {
    // First render: queued state
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 1, queueReason: 'slot_full' }),
    )
    const { rerender } = render(
      <ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Queued — position 1')
    expect(screen.queryByTestId('tool-output')).not.toBeInTheDocument()

    // Second render: started — queued cleared, output streaming
    mockedUseToolRunStream.mockReturnValue(
      streamResult({
        queued: false,
        queuePosition: null,
        queueReason: null,
        lines: [{ stream: 'stdout', text: 'scan started' }],
      }),
    )
    rerender(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    // Badge is gone; output pane and running spinner appear
    expect(screen.queryByText(/queued/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('tool-output')).toBeInTheDocument()
    expect(screen.getByText('scan started')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/running/i)
  })

  // --- Stop button tests ---

  it('shows a Stop button while the run is running', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: false, queued: false }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('stop-button')).toBeInTheDocument()
  })

  it('shows a Stop button while the run is queued', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 1, queueReason: 'slot_full' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('stop-button')).toBeInTheDocument()
  })

  it('shows a Stop button while the run is awaiting a timeout decision', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('stop-button')).toBeInTheDocument()
  })

  it('Stop button calls useKillToolRun with the toolRunId', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: false }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('stop-button'))
    expect(killMutateFn).toHaveBeenCalledWith(TOOL_RUN_ID)
  })

  it('Stop button fires kill when the run is queued', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ queued: true, queuePosition: 1, queueReason: 'slot_full' }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('stop-button'))
    expect(killMutateFn).toHaveBeenCalledWith(TOOL_RUN_ID)
  })

  it('Stop button fires kill when the run is awaiting a timeout decision', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('stop-button'))
    expect(killMutateFn).toHaveBeenCalledWith(TOOL_RUN_ID)
  })

  it('hides the Stop button when the run is done', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: true, exitCode: 0 }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.queryByTestId('stop-button')).not.toBeInTheDocument()
  })

  // --- Killed badge tests ---

  it('renders a Killed badge when the stream reports killed', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ isDone: true, killed: true }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('killed-badge')).toBeInTheDocument()
    expect(screen.getByTestId('killed-badge')).toHaveTextContent('Killed')
  })

  it('does not show the Stop button when the run is killed (isDone=true)', () => {
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ isDone: true, killed: true }),
    )
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.queryByTestId('stop-button')).not.toBeInTheDocument()
  })

  // --- Timeout prompt tests ---

  it('renders the timeout prompt when awaitingTimeout is set', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('timeout-prompt')).toBeInTheDocument()
    expect(screen.getByText(/timed out — what do you want to do/i)).toBeInTheDocument()
  })

  it('timeout prompt renders all three buttons', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByTestId('timeout-kill-button')).toBeInTheDocument()
    expect(screen.getByTestId('timeout-extend-button')).toBeInTheDocument()
    expect(screen.getByTestId('timeout-wait-button')).toBeInTheDocument()
  })

  it('Kill button in timeout prompt fires decision=kill', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('timeout-kill-button'))
    expect(timeoutDecisionMutateFn).toHaveBeenCalledWith({
      toolRunId: TOOL_RUN_ID,
      decision: 'kill',
      extend_seconds: 30,
    })
  })

  it('Extend button in timeout prompt fires decision=extend with extend_seconds=30', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('timeout-extend-button'))
    expect(timeoutDecisionMutateFn).toHaveBeenCalledWith({
      toolRunId: TOOL_RUN_ID,
      decision: 'extend',
      extend_seconds: 30,
    })
  })

  it('Wait button in timeout prompt fires decision=wait', async () => {
    const user = userEvent.setup()
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    await user.click(screen.getByTestId('timeout-wait-button'))
    expect(timeoutDecisionMutateFn).toHaveBeenCalledWith({
      toolRunId: TOOL_RUN_ID,
      decision: 'wait',
      extend_seconds: 30,
    })
  })

  it('timeout prompt shows no kill countdown or timer text', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)

    const prompt = screen.getByTestId('timeout-prompt')
    // No countdown / auto-kill / seconds-remaining text
    expect(prompt.textContent).not.toMatch(/auto.?kill/i)
    expect(prompt.textContent).not.toMatch(/seconds? remaining/i)
    expect(prompt.textContent).not.toMatch(/countdown/i)
    expect(prompt.textContent).not.toMatch(/grace/i)
    // Confirm the "stays open" copy IS present to reassure the user
    expect(prompt.textContent).toMatch(/stays open/i)
  })

  it('timeout prompt notes that the slot was released', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ awaitingTimeout: true }))
    render(<ToolOutputConsole engagementId={ENGAGEMENT_ID} toolRunId={TOOL_RUN_ID} />)
    const prompt = screen.getByTestId('timeout-prompt')
    expect(prompt.textContent).toMatch(/slot.*released|released.*slot/i)
  })
})
