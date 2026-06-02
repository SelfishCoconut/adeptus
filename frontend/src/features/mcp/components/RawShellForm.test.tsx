import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RawShellForm } from './RawShellForm'
import { useEngagements } from '@/features/engagements/api'
import { useExecuteToolRun } from '../api'
import type { EngagementSummary, ToolRunResult } from '@/shared/api'

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock('@/features/engagements/api', () => ({
  useEngagements: vi.fn(),
}))

vi.mock('../api', () => ({
  useExecuteToolRun: vi.fn(),
}))

const mockedUseEngagements = vi.mocked(useEngagements)
const mockedUseExecuteToolRun = vi.mocked(useExecuteToolRun)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_A: EngagementSummary = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Alpha Pentest',
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  member_role: 'owner',
  privacy_mode: 'local_only',
}

const ENGAGEMENT_B: EngagementSummary = {
  id: '00000000-0000-0000-0000-000000000002',
  name: 'Beta Audit',
  status: 'active',
  created_at: '2026-02-01T00:00:00Z',
  member_role: 'member',
  privacy_mode: 'local_only',
}

const TOOL_RUN_RESULT: ToolRunResult = {
  tool_run_id: '00000000-0000-0000-0000-000000000099',
  engagement_id: ENGAGEMENT_A.id,
  server_name: 'shell-exec',
  tool_name: 'run_command',
  exit_code: 0,
  stdout: 'hello\n',
  stderr: '',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:00:01Z',
  status: 'completed',
  preset_name: null,
}

const TRUNCATED_RESULT: ToolRunResult = {
  ...TOOL_RUN_RESULT,
  stdout: 'lots of output...[output truncated at 1 MB]',
  stderr: '',
}

// ---------------------------------------------------------------------------
// Helper factories
// ---------------------------------------------------------------------------

function engagementsResult(overrides: Partial<ReturnType<typeof useEngagements>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useEngagements>
}

/**
 * Build a fake mutation result.
 * The `mutate` function optionally calls onSuccess/onError so tests can drive
 * both happy and error paths without a real network call.
 */
function mutationResult(overrides: {
  mutate?: ReturnType<typeof useExecuteToolRun>['mutate']
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
  } as unknown as ReturnType<typeof useExecuteToolRun>
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderForm(props: { initialEngagementId?: string } = {}) {
  return render(<RawShellForm {...props} />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('RawShellForm', () => {
  beforeEach(() => {
    mockedUseEngagements.mockReset()
    mockedUseExecuteToolRun.mockReset()
  })

  describe('form rendering', () => {
    it('renders command input, timeout input, engagement selector and Run button', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({}))

      renderForm()

      expect(screen.getByLabelText(/command/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/timeout/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/engagement/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument()
    })

    it('timeout input defaults to 30', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({}))

      renderForm()

      const timeoutInput = screen.getByLabelText(/timeout/i)
      expect(timeoutInput).toHaveValue(30)
    })

    it('populates the engagement selector with engagement names', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({}))

      renderForm()

      expect(screen.getByRole('option', { name: 'Alpha Pentest' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: 'Beta Audit' })).toBeInTheDocument()
    })
  })

  describe('submit with correct args', () => {
    it('calls mutation with server_name, tool_name, args.command, and timeout_seconds', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'echo hello')
      // Use triple-click to select all text then type replacement value
      await user.tripleClick(screen.getByLabelText(/timeout/i))
      await user.keyboard('60')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(mutate).toHaveBeenCalledOnce()
      expect(mutate).toHaveBeenCalledWith(
        {
          engagement_id: ENGAGEMENT_A.id,
          server_name: 'shell-exec',
          tool_name: 'run_command',
          args: { command: 'echo hello' },
          timeout_seconds: 60,
          async_mode: false,
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      )
    })

    it('uses the default timeout of 30 when not changed', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'ls')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(mutate).toHaveBeenCalledWith(
        expect.objectContaining({ timeout_seconds: 30 }),
        expect.anything(),
      )
    })

    it('uses the selected engagement_id when the selector is changed', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.selectOptions(screen.getByLabelText(/engagement/i), ENGAGEMENT_B.id)
      await user.type(screen.getByLabelText(/command/i), 'whoami')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(mutate).toHaveBeenCalledWith(
        expect.objectContaining({ engagement_id: ENGAGEMENT_B.id }),
        expect.anything(),
      )
    })
  })

  describe('loading state', () => {
    it('shows "Running…" on the button while mutation is pending', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ isPending: true }))

      renderForm()

      expect(screen.getByRole('button', { name: /running…/i })).toBeDisabled()
    })

    it('disables Run button while pending', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ isPending: true }))

      renderForm()

      expect(screen.getByRole('button', { name: /running…/i })).toBeDisabled()
    })
  })

  describe('result rendering', () => {
    it('renders stdout, stderr and exit_code on success', async () => {
      const user = userEvent.setup()

      // mutate calls onSuccess with the result
      const mutate = vi.fn(
        (_body: unknown, options?: Record<string, unknown>) => {
          const cb = options?.['onSuccess']
          if (typeof cb === 'function') cb(TOOL_RUN_RESULT)
        },
      ) as unknown as ReturnType<typeof useExecuteToolRun>['mutate']

      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'echo hello')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      // Exit code
      expect(screen.getByText('0')).toBeInTheDocument()
      // stdout in a <pre>
      const preBlocks = screen.getAllByRole('generic').filter(
        (el) => el.tagName === 'PRE',
      )
      expect(preBlocks.length).toBeGreaterThanOrEqual(1)
      // Use a function matcher to handle the literal newline inside <pre>
      expect(
        screen.getByText((content) => content.includes('hello')),
      ).toBeInTheDocument()
    })

    it('renders exit_code, stdout and stderr pre blocks', async () => {
      const user = userEvent.setup()

      const resultWithStderr: ToolRunResult = {
        ...TOOL_RUN_RESULT,
        exit_code: 1,
        stdout: '',
        stderr: 'command not found',
      }

      const mutate = vi.fn(
        (_body: unknown, options?: Record<string, unknown>) => {
          const cb = options?.['onSuccess']
          if (typeof cb === 'function') cb(resultWithStderr)
        },
      ) as unknown as ReturnType<typeof useExecuteToolRun>['mutate']

      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'bad-cmd')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(screen.getByText('1')).toBeInTheDocument()
      expect(screen.getByText('command not found')).toBeInTheDocument()
    })
  })

  describe('truncation notice', () => {
    it('shows truncation notice when sentinel is in stdout', async () => {
      const user = userEvent.setup()

      const mutate = vi.fn(
        (_body: unknown, options?: Record<string, unknown>) => {
          const cb = options?.['onSuccess']
          if (typeof cb === 'function') cb(TRUNCATED_RESULT)
        },
      ) as unknown as ReturnType<typeof useExecuteToolRun>['mutate']

      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'cat /dev/urandom')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(screen.getByRole('status')).toHaveTextContent('Output was truncated at 1 MB.')
    })

    it('shows truncation notice when sentinel is in stderr', async () => {
      const user = userEvent.setup()

      const truncatedStderr: ToolRunResult = {
        ...TOOL_RUN_RESULT,
        stdout: '',
        stderr: 'big output...[output truncated at 1 MB]',
      }

      const mutate = vi.fn(
        (_body: unknown, options?: Record<string, unknown>) => {
          const cb = options?.['onSuccess']
          if (typeof cb === 'function') cb(truncatedStderr)
        },
      ) as unknown as ReturnType<typeof useExecuteToolRun>['mutate']

      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'cat /dev/urandom 1>&2')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(screen.getByRole('status')).toHaveTextContent('Output was truncated at 1 MB.')
    })

    it('does not show truncation notice for normal output', async () => {
      const user = userEvent.setup()

      const mutate = vi.fn(
        (_body: unknown, options?: Record<string, unknown>) => {
          const cb = options?.['onSuccess']
          if (typeof cb === 'function') cb(TOOL_RUN_RESULT)
        },
      ) as unknown as ReturnType<typeof useExecuteToolRun>['mutate']

      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm()

      await user.type(screen.getByLabelText(/command/i), 'echo hello')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  describe('initialEngagementId prop', () => {
    it('defaults selector to initialEngagementId when provided', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({}))

      renderForm({ initialEngagementId: ENGAGEMENT_B.id })

      const selector = screen.getByLabelText(/engagement/i) as HTMLSelectElement
      expect(selector.value).toBe(ENGAGEMENT_B.id)
    })

    it('falls back to first engagement when initialEngagementId is not provided', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({}))

      renderForm()

      const selector = screen.getByLabelText(/engagement/i) as HTMLSelectElement
      expect(selector.value).toBe(ENGAGEMENT_A.id)
    })

    it('submit sends initialEngagementId when provided and user has not changed selector', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A, ENGAGEMENT_B] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(mutationResult({ mutate }))

      renderForm({ initialEngagementId: ENGAGEMENT_B.id })

      await user.type(screen.getByLabelText(/command/i), 'whoami')
      await user.click(screen.getByRole('button', { name: /^run$/i }))

      expect(mutate).toHaveBeenCalledWith(
        expect.objectContaining({ engagement_id: ENGAGEMENT_B.id }),
        expect.anything(),
      )
    })
  })

  describe('error state', () => {
    it('shows error banner when mutation fails (e.g. 503)', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(
        mutationResult({
          isError: true,
          error: new Error('Failed to execute tool run'),
        }),
      )

      renderForm()

      expect(screen.getByRole('alert')).toHaveTextContent('Failed to execute tool run')
    })

    it('shows fallback error message when error is not an Error instance', () => {
      mockedUseEngagements.mockReturnValue(
        engagementsResult({ data: [ENGAGEMENT_A] }),
      )
      mockedUseExecuteToolRun.mockReturnValue(
        mutationResult({
          isError: true,
          error: 'string error' as unknown as Error,
        }),
      )

      renderForm()

      expect(screen.getByRole('alert')).toHaveTextContent('Failed to execute tool run.')
    })
  })
})
