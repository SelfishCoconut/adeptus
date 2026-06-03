import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolRunnerForm } from './ToolRunnerForm'
import { useExecuteToolRunAsync, useListTools } from '../api'
import type { ToolDescriptor, ToolRunResult } from '@/shared/api'

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock('../api', () => ({
  useListTools: vi.fn(),
  useExecuteToolRunAsync: vi.fn(),
}))

const mockedUseListTools = vi.mocked(useListTools)
const mockedUseExecuteToolRunAsync = vi.mocked(useExecuteToolRunAsync)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const TOOL_RUN_ID = '00000000-0000-0000-0000-000000000002'

const HTTPX_DESCRIPTOR: ToolDescriptor = {
  server_name: 'httpx',
  tool_name: 'run_httpx',
  weight: 'light',
  capability_flags: ['network'],
  presets: [
    { name: 'quick', description: 'fast scan', args: { flags: ['-sc', '-title'] } },
    { name: 'full', args: { flags: ['-sc', '-title', '-tech-detect'] } },
  ],
  arg_schema: {
    type: 'object',
    properties: {
      target: { type: 'string', description: 'URL to probe' },
      flags: { type: 'array', items: { type: 'string' } },
      timeout_seconds: { type: 'integer' },
    },
    required: ['target'],
  },
}

const RUNNING_RESULT: ToolRunResult = {
  tool_run_id: TOOL_RUN_ID,
  engagement_id: ENGAGEMENT_ID,
  server_name: 'httpx',
  tool_name: 'run_httpx',
  exit_code: null,
  stdout: '',
  stderr: '',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: null,
  status: 'running',
  preset_name: 'quick',
}

// ---------------------------------------------------------------------------
// Helper factories
// ---------------------------------------------------------------------------

function listToolsResult(overrides: Partial<ReturnType<typeof useListTools>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useListTools>
}

function mutationResult(overrides: {
  mutate?: (...args: never[]) => void
  isPending?: boolean
  isError?: boolean
  error?: Error | null
}) {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    mutateAsync: vi.fn(),
    isPending: overrides.isPending ?? false,
    isError: overrides.isError ?? false,
    isIdle: true,
    isSuccess: false,
    error: overrides.error ?? null,
    data: undefined,
    reset: vi.fn(),
    status: 'idle' as const,
    variables: undefined,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    isPaused: false,
    submittedAt: 0,
  } as unknown as ReturnType<typeof useExecuteToolRunAsync>
}

function renderForm(props: { onRunStarted?: (id: string) => void } = {}) {
  return render(
    <ToolRunnerForm engagementId={ENGAGEMENT_ID} onRunStarted={props.onRunStarted ?? vi.fn()} />,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ToolRunnerForm', () => {
  beforeEach(() => {
    mockedUseListTools.mockReset()
    mockedUseExecuteToolRunAsync.mockReset()
  })

  it('populates the tool selector grouped by server name', () => {
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()

    expect(screen.getByRole('option', { name: 'run_httpx' })).toBeInTheDocument()
    // optgroup label
    expect(screen.getByRole('group', { name: 'httpx' })).toBeInTheDocument()
  })

  it('shows a loading state while tools load', () => {
    mockedUseListTools.mockReturnValue(listToolsResult({ isLoading: true }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()
    expect(screen.getByText(/loading tools/i)).toBeInTheDocument()
  })

  it('reveals preset options and arg fields once httpx is selected', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')

    expect(screen.getByRole('option', { name: /quick/i })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /full/i })).toBeInTheDocument()
    // arg fields rendered from the schema
    expect(screen.getByLabelText('target')).toBeInTheDocument()
    expect(screen.getByLabelText('flags')).toBeInTheDocument()
    expect(screen.getByLabelText('timeout_seconds')).toBeInTheDocument()
  })

  it('pre-fills the target field with the sandbox URL', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')

    expect(screen.getByLabelText('target')).toHaveValue('http://localhost:3000')
  })

  it('shows the sandbox guard notice in dev mode', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')

    // import.meta.env.DEV is true under vitest
    expect(screen.getByRole('status')).toHaveTextContent(/sandbox/i)
  })

  it('applies preset args to the form fields', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    renderForm()
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')
    await user.selectOptions(screen.getByLabelText(/preset/i), 'quick')

    expect(screen.getByLabelText('flags')).toHaveValue('-sc -title')
  })

  it('fires the async mutation with typed args on submit', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({ mutate }))

    renderForm()
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')
    await user.selectOptions(screen.getByLabelText(/preset/i), 'quick')
    await user.click(screen.getByRole('button', { name: /^run$/i }))

    expect(mutate).toHaveBeenCalledOnce()
    const [body] = mutate.mock.calls[0]
    expect(body).toMatchObject({
      engagement_id: ENGAGEMENT_ID,
      server_name: 'httpx',
      tool_name: 'run_httpx',
      preset_name: 'quick',
      async_mode: true,
      timeout_seconds: 30,
    })
    expect(body.args).toEqual({
      target: 'http://localhost:3000',
      flags: ['-sc', '-title'],
      timeout_seconds: 30,
    })
  })

  it('invokes onRunStarted with the returned tool_run_id on success', async () => {
    const user = userEvent.setup()
    const onRunStarted = vi.fn()
    const mutate = vi.fn((_body, opts?: { onSuccess?: (d: ToolRunResult) => void }) => {
      opts?.onSuccess?.(RUNNING_RESULT)
    })
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({ mutate }))

    renderForm({ onRunStarted })
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')
    await user.click(screen.getByRole('button', { name: /^run$/i }))

    expect(onRunStarted).toHaveBeenCalledWith(TOOL_RUN_ID)
  })

  it('renders an error banner when the mutation fails', () => {
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(
      mutationResult({ isError: true, error: new Error('Target outside sandbox') }),
    )

    renderForm()
    expect(screen.getByRole('alert')).toHaveTextContent(/target outside sandbox/i)
  })

  it('disables the Run button when paused=true is passed', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    render(
      <ToolRunnerForm
        engagementId={ENGAGEMENT_ID}
        onRunStarted={vi.fn()}
        paused={true}
      />,
    )
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')

    expect(screen.getByRole('button', { name: /^run$/i })).toBeDisabled()
  })

  it('enables the Run button when paused=false', async () => {
    const user = userEvent.setup()
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(mutationResult({}))

    render(
      <ToolRunnerForm
        engagementId={ENGAGEMENT_ID}
        onRunStarted={vi.fn()}
        paused={false}
      />,
    )
    await user.selectOptions(screen.getByLabelText(/^tool$/i), 'httpx/run_httpx')

    expect(screen.getByRole('button', { name: /^run$/i })).not.toBeDisabled()
  })

  it('surfaces a clear message when a 409 (engagement paused) is returned', () => {
    mockedUseListTools.mockReturnValue(listToolsResult({ data: [HTTPX_DESCRIPTOR] }))
    mockedUseExecuteToolRunAsync.mockReturnValue(
      mutationResult({
        isError: true,
        error: new Error('Engagement is paused — no tool runs may start while paused'),
      }),
    )

    renderForm()
    expect(screen.getByRole('alert')).toHaveTextContent(/engagement is paused/i)
  })
})
