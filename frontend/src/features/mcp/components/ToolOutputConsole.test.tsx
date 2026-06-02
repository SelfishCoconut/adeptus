import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ToolOutputConsole } from './ToolOutputConsole'
import { useToolRunStream, type ToolRunStream } from '../hooks/useToolRunStream'

vi.mock('../hooks/useToolRunStream', () => ({
  useToolRunStream: vi.fn(),
}))

const mockedUseToolRunStream = vi.mocked(useToolRunStream)

function streamResult(overrides: Partial<ToolRunStream>): ToolRunStream {
  return { lines: [], isDone: false, exitCode: null, ...overrides }
}

const TOOL_RUN_ID = '00000000-0000-0000-0000-000000000001'

beforeEach(() => {
  mockedUseToolRunStream.mockReset()
  // jsdom does not implement scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn()
})

describe('ToolOutputConsole', () => {
  it('shows a placeholder when no run is active', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({}))
    render(<ToolOutputConsole toolRunId={null} />)
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
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)

    expect(screen.getByText('first line')).toBeInTheDocument()
    expect(screen.getByText('second line')).toBeInTheDocument()
  })

  it('shows a running spinner while the run is in progress', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: false }))
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByRole('status')).toHaveTextContent(/running/i)
  })

  it('shows a Completed badge with exit 0 on success', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: true, exitCode: 0 }))
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)
    expect(screen.getByText(/completed · exit 0/i)).toBeInTheDocument()
  })

  it('shows a Failed badge for a non-zero exit code', () => {
    mockedUseToolRunStream.mockReturnValue(streamResult({ isDone: true, exitCode: 2 }))
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)
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
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)

    expect(screen.getByText('ok line')).not.toHaveClass('text-red-400')
    expect(screen.getByText('error line')).toHaveClass('text-red-400')
  })

  it('auto-scrolls to the latest line when output changes', () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    mockedUseToolRunStream.mockReturnValue(
      streamResult({ lines: [{ stream: 'stdout', text: 'line' }] }),
    )
    render(<ToolOutputConsole toolRunId={TOOL_RUN_ID} />)
    expect(scrollIntoView).toHaveBeenCalled()
  })
})
