import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolQueueStrip } from './ToolQueueStrip'
import { useToolQueue } from '../api'
import type { ToolQueueSnapshot, QueuedRun } from '@/shared/api'

vi.mock('../api', () => ({
  useToolQueue: vi.fn(),
}))

const mockedUseToolQueue = vi.mocked(useToolQueue)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeSnapshot(overrides: Partial<ToolQueueSnapshot>): ToolQueueSnapshot {
  return {
    slot_limit: 3,
    running_count: 0,
    queued_count: 0,
    queued: [],
    ...overrides,
  }
}

function makeQueuedRun(overrides: Partial<QueuedRun>): QueuedRun {
  return {
    tool_run_id: '00000000-0000-0000-0000-0000000000aa',
    server_name: 'httpx',
    tool_name: 'run_httpx_heavy',
    target_host: 'localhost',
    position: 1,
    reason: 'slot_full',
    enqueued_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function hookResult(snapshot: ToolQueueSnapshot | undefined): ReturnType<typeof useToolQueue> {
  return {
    data: snapshot,
    isLoading: snapshot === undefined,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useToolQueue>
}

beforeEach(() => {
  mockedUseToolQueue.mockReset()
})

describe('ToolQueueStrip', () => {
  it('renders nothing (empty state) when running_count and queued_count are both 0', () => {
    mockedUseToolQueue.mockReturnValue(hookResult(makeSnapshot({ running_count: 0, queued_count: 0 })))
    const { container } = render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing while data is still loading', () => {
    mockedUseToolQueue.mockReturnValue(hookResult(undefined))
    const { container } = render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the counts when there are running runs', () => {
    mockedUseToolQueue.mockReturnValue(hookResult(makeSnapshot({ running_count: 2, queued_count: 0 })))
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByTestId('queue-counts')).toHaveTextContent('2 running / 0 queued')
  })

  it('renders the counts when there are queued runs', () => {
    mockedUseToolQueue.mockReturnValue(hookResult(makeSnapshot({ running_count: 2, queued_count: 3 })))
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByTestId('queue-counts')).toHaveTextContent('2 running / 3 queued')
  })

  it('does not show the queued list before the strip is expanded', () => {
    const queued = [
      makeQueuedRun({ tool_run_id: 'a', position: 1, tool_name: 'run_httpx_heavy' }),
    ]
    mockedUseToolQueue.mockReturnValue(
      hookResult(makeSnapshot({ running_count: 1, queued_count: 1, queued })),
    )
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(screen.queryByTestId('queue-list')).not.toBeInTheDocument()
  })

  it('expands the queued list with tool name and position when the strip is clicked', async () => {
    const user = userEvent.setup()
    const queued = [
      makeQueuedRun({ tool_run_id: 'a', position: 1, tool_name: 'run_httpx_heavy' }),
      makeQueuedRun({ tool_run_id: 'b', position: 2, tool_name: 'sleep_probe' }),
    ]
    mockedUseToolQueue.mockReturnValue(
      hookResult(makeSnapshot({ running_count: 1, queued_count: 2, queued })),
    )
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: /tool queue status/i }))

    expect(screen.getByTestId('queue-list')).toBeInTheDocument()
    expect(screen.getByTestId('queue-item-1')).toHaveTextContent('#1')
    expect(screen.getByTestId('queue-item-1')).toHaveTextContent('run_httpx_heavy')
    expect(screen.getByTestId('queue-item-2')).toHaveTextContent('#2')
    expect(screen.getByTestId('queue-item-2')).toHaveTextContent('sleep_probe')
  })

  it('collapses the list again on a second click', async () => {
    const user = userEvent.setup()
    const queued = [makeQueuedRun({ tool_run_id: 'a', position: 1 })]
    mockedUseToolQueue.mockReturnValue(
      hookResult(makeSnapshot({ running_count: 1, queued_count: 1, queued })),
    )
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: /tool queue status/i }))
    expect(screen.getByTestId('queue-list')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /tool queue status/i }))
    expect(screen.queryByTestId('queue-list')).not.toBeInTheDocument()
  })

  it('hides the list section when expanded but queued array is empty (only running)', async () => {
    const user = userEvent.setup()
    mockedUseToolQueue.mockReturnValue(
      hookResult(makeSnapshot({ running_count: 3, queued_count: 0, queued: [] })),
    )
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)

    await user.click(screen.getByRole('button', { name: /tool queue status/i }))
    // Strip is visible but the queued list section is absent (nothing to list)
    expect(screen.getByTestId('tool-queue-strip')).toBeInTheDocument()
    expect(screen.queryByTestId('queue-list')).not.toBeInTheDocument()
  })

  it('calls useToolQueue with the supplied engagementId', () => {
    mockedUseToolQueue.mockReturnValue(hookResult(makeSnapshot({ running_count: 1, queued_count: 0 })))
    render(<ToolQueueStrip engagementId={ENGAGEMENT_ID} />)
    expect(mockedUseToolQueue).toHaveBeenCalledWith(ENGAGEMENT_ID)
  })
})
