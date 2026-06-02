import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolRunHistory } from './ToolRunHistory'
import { useListToolRuns } from '../api'
import type { ToolRunResult } from '@/shared/api'

vi.mock('../api', () => ({
  useListToolRuns: vi.fn(),
}))

const mockedUseListToolRuns = vi.mocked(useListToolRuns)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function run(overrides: Partial<ToolRunResult>): ToolRunResult {
  return {
    tool_run_id: '00000000-0000-0000-0000-0000000000aa',
    engagement_id: ENGAGEMENT_ID,
    server_name: 'httpx',
    tool_name: 'run_httpx',
    exit_code: 0,
    stdout: '',
    stderr: '',
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
    status: 'completed',
    preset_name: 'quick',
    ...overrides,
  }
}

function listResult(overrides: Partial<ReturnType<typeof useListToolRuns>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useListToolRuns>
}

function renderHistory(props: { onSelectRun?: (id: string) => void } = {}) {
  return render(
    <ToolRunHistory engagementId={ENGAGEMENT_ID} onSelectRun={props.onSelectRun ?? vi.fn()} />,
  )
}

beforeEach(() => {
  mockedUseListToolRuns.mockReset()
})

describe('ToolRunHistory', () => {
  it('shows a loading state', () => {
    mockedUseListToolRuns.mockReturnValue(listResult({ isLoading: true }))
    renderHistory()
    expect(screen.getByText(/loading run history/i)).toBeInTheDocument()
  })

  it('shows an empty state when there are no runs', () => {
    mockedUseListToolRuns.mockReturnValue(
      listResult({ data: { pages: [{ items: [], next_cursor: null }], pageParams: [null] } }),
    )
    renderHistory()
    expect(screen.getByText(/no tool runs yet/i)).toBeInTheDocument()
  })

  it('renders a row per run with tool name, preset, status and exit code', () => {
    mockedUseListToolRuns.mockReturnValue(
      listResult({
        data: {
          pages: [{ items: [run({}), run({ tool_run_id: 'b', status: 'failed', exit_code: 1 })], next_cursor: null }],
          pageParams: [null],
        },
      }),
    )
    renderHistory()

    expect(screen.getAllByText('run_httpx')).toHaveLength(2)
    expect(screen.getAllByText('quick')).toHaveLength(2)
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.getByText('exit 1')).toBeInTheDocument()
  })

  it('calls onSelectRun with the run id when a row is clicked', async () => {
    const user = userEvent.setup()
    const onSelectRun = vi.fn()
    mockedUseListToolRuns.mockReturnValue(
      listResult({
        data: {
          pages: [{ items: [run({ tool_run_id: 'run-123' })], next_cursor: null }],
          pageParams: [null],
        },
      }),
    )
    renderHistory({ onSelectRun })

    await user.click(screen.getByRole('button', { name: /run_httpx/i }))
    expect(onSelectRun).toHaveBeenCalledWith('run-123')
  })

  it('shows a Load more button that fetches the next page', async () => {
    const user = userEvent.setup()
    const fetchNextPage = vi.fn()
    mockedUseListToolRuns.mockReturnValue(
      listResult({
        data: { pages: [{ items: [run({})], next_cursor: 'CURSOR' }], pageParams: [null] },
        hasNextPage: true,
        fetchNextPage,
      }),
    )
    renderHistory()

    await user.click(screen.getByRole('button', { name: /load more/i }))
    expect(fetchNextPage).toHaveBeenCalled()
  })

  it('flattens multiple pages into one list', () => {
    mockedUseListToolRuns.mockReturnValue(
      listResult({
        data: {
          pages: [
            { items: [run({ tool_run_id: 'a' })], next_cursor: 'C1' },
            { items: [run({ tool_run_id: 'b' })], next_cursor: null },
          ],
          pageParams: [null, 'C1'],
        },
      }),
    )
    renderHistory()
    expect(screen.getAllByRole('button', { name: /run_httpx/i })).toHaveLength(2)
  })
})
