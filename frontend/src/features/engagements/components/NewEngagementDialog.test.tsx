import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NewEngagementDialog } from './NewEngagementDialog'
import { useCreateEngagement } from '../api'

vi.mock('../api', () => ({
  useCreateEngagement: vi.fn(),
}))

const mockedUseCreateEngagement = vi.mocked(useCreateEngagement)

// Build a fake mutation result matching the ReturnType of useCreateEngagement.
// The mutate function optionally invokes onSuccess/onError so tests can drive
// the success and error paths without a real network call.
function mutationResult(overrides: {
  mutate?: ReturnType<typeof useCreateEngagement>['mutate']
  isPending?: boolean
  error?: unknown
}) {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    isPending: overrides.isPending ?? false,
    error: overrides.error ?? null,
    isIdle: true,
    isSuccess: false,
    isError: !!overrides.error,
    data: undefined,
    reset: vi.fn(),
    mutateAsync: vi.fn(),
    status: 'idle' as const,
    variables: undefined,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    isPaused: false,
    submittedAt: 0,
  } as unknown as ReturnType<typeof useCreateEngagement>
}

function renderDialog(onOpenChange = vi.fn()) {
  return {
    onOpenChange,
    ...render(<NewEngagementDialog open={true} onOpenChange={onOpenChange} />),
  }
}

describe('NewEngagementDialog', () => {
  beforeEach(() => {
    mockedUseCreateEngagement.mockReset()
  })

  it('renders form fields — Name, Scope, and Client Info inputs are present', () => {
    mockedUseCreateEngagement.mockReturnValue(mutationResult({}))

    renderDialog()

    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/scope/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/client info/i)).toBeInTheDocument()
  })

  it('submits and calls mutation with the expected body', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseCreateEngagement.mockReturnValue(mutationResult({ mutate }))

    renderDialog()

    await user.type(screen.getByLabelText(/name/i), 'ACME Pentest')
    await user.type(screen.getByLabelText(/scope/i), '192.168.1.0/24')
    await user.type(screen.getByLabelText(/client info/i), 'Jane Doe, ACME Corp')

    await user.click(screen.getByRole('button', { name: /create/i }))

    expect(mutate).toHaveBeenCalledOnce()
    expect(mutate).toHaveBeenCalledWith(
      { name: 'ACME Pentest', scope: '192.168.1.0/24', client_info: 'Jane Doe, ACME Corp' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it('shows field error on 422 response', () => {
    const validationError = {
      detail: [
        {
          loc: ['body', 'name'],
          msg: 'String should have at least 1 character',
          type: 'string_too_short',
        },
      ],
    }
    mockedUseCreateEngagement.mockReturnValue(mutationResult({ error: validationError }))

    renderDialog()

    expect(screen.getByRole('alert')).toHaveTextContent(
      'String should have at least 1 character',
    )
  })

  it('shows scope field error on 422 response for scope', () => {
    const validationError = {
      detail: [
        {
          loc: ['body', 'scope'],
          msg: 'Scope is required',
          type: 'string_too_short',
        },
      ],
    }
    mockedUseCreateEngagement.mockReturnValue(mutationResult({ error: validationError }))

    renderDialog()

    expect(screen.getByRole('alert')).toHaveTextContent('Scope is required')
  })

  it('shows client_info field error on 422 response for client_info', () => {
    const validationError = {
      detail: [
        {
          loc: ['body', 'client_info'],
          msg: 'Client info too long',
          type: 'string_too_long',
        },
      ],
    }
    mockedUseCreateEngagement.mockReturnValue(mutationResult({ error: validationError }))

    renderDialog()

    expect(screen.getByRole('alert')).toHaveTextContent('Client info too long')
  })

  it('closes the dialog when Cancel is clicked', async () => {
    const user = userEvent.setup()
    mockedUseCreateEngagement.mockReturnValue(mutationResult({}))

    const onOpenChange = vi.fn()
    render(<NewEngagementDialog open={true} onOpenChange={onOpenChange} />)

    await user.click(screen.getByRole('button', { name: /cancel/i }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('closes the dialog on success', async () => {
    const user = userEvent.setup()
    // Cast to the expected type via unknown — the mock only needs to call
    // onSuccess() without caring about the full MutateOptions signature.
    const mutate = vi.fn((_body: unknown, options?: Record<string, unknown>) => {
      const cb = options?.['onSuccess']
      if (typeof cb === 'function') cb()
    }) as unknown as ReturnType<typeof useCreateEngagement>['mutate']
    mockedUseCreateEngagement.mockReturnValue(mutationResult({ mutate }))

    const onOpenChange = vi.fn()
    render(<NewEngagementDialog open={true} onOpenChange={onOpenChange} />)

    await user.type(screen.getByLabelText(/name/i), 'Test')
    await user.type(screen.getByLabelText(/scope/i), '10.0.0.0/8')
    await user.click(screen.getByRole('button', { name: /create/i }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('clears error banner when dialog is closed and reopened (W-04)', async () => {
    const user = userEvent.setup()

    // First render: dialog open with an active error (post-submit 422 state).
    const validationError = {
      detail: [{ loc: ['body', 'name'], msg: 'Required', type: 'missing' }],
    }
    const reset = vi.fn()
    const mutationWithError = {
      ...mutationResult({ error: validationError }),
      reset,
      isError: true,
    } as unknown as ReturnType<typeof useCreateEngagement>
    mockedUseCreateEngagement.mockReturnValue(mutationWithError)

    const { rerender } = render(
      <NewEngagementDialog open={true} onOpenChange={vi.fn()} />,
    )

    // Error banner is visible while the dialog is open with an error.
    expect(screen.getByRole('alert')).toBeInTheDocument()

    // Simulate closing: click Cancel (goes through handleOpenChange → resetFields → reset()).
    // Re-mock with a clean mutation before closing so the rerender sees no error.
    const cleanMutation = mutationResult({})
    mockedUseCreateEngagement.mockReturnValue(cleanMutation)

    await user.click(screen.getByRole('button', { name: /cancel/i }))

    // Now rerender with open=false then open=true to simulate close/reopen cycle.
    rerender(<NewEngagementDialog open={false} onOpenChange={vi.fn()} />)
    rerender(<NewEngagementDialog open={true} onOpenChange={vi.fn()} />)

    // reset() must have been called when the dialog closed.
    expect(reset).toHaveBeenCalled()
    // No error banner on reopen.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
