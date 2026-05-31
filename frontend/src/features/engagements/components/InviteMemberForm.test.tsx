import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InviteMemberForm } from './InviteMemberForm'
import { useAddMember } from '../api'

vi.mock('../api', () => ({
  useAddMember: vi.fn(),
}))

const mockedUseAddMember = vi.mocked(useAddMember)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function addMutationResult(overrides: {
  mutate?: ReturnType<typeof useAddMember>['mutate']
  isPending?: boolean
  isError?: boolean
  error?: Error | null
} = {}) {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    isPending: overrides.isPending ?? false,
    isError: overrides.isError ?? false,
    error: overrides.error ?? null,
    isIdle: true,
    isSuccess: false,
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
  } as unknown as ReturnType<typeof useAddMember>
}

function renderForm(callerRole: 'owner' | 'member' = 'owner') {
  return render(<InviteMemberForm engagementId={ENGAGEMENT_ID} callerRole={callerRole} />)
}

describe('InviteMemberForm', () => {
  beforeEach(() => {
    mockedUseAddMember.mockReset()
  })

  it('renders the invite form for owner', () => {
    mockedUseAddMember.mockReturnValue(addMutationResult())

    renderForm('owner')

    expect(screen.getByLabelText(/invite member/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /invite/i })).toBeInTheDocument()
  })

  it('does not render the form for non-owner', () => {
    mockedUseAddMember.mockReturnValue(addMutationResult())

    renderForm('member')

    expect(screen.queryByLabelText(/invite member/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /invite/i })).not.toBeInTheDocument()
  })

  it('submits and calls mutation with the entered username', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseAddMember.mockReturnValue(addMutationResult({ mutate }))

    renderForm('owner')

    await user.type(screen.getByLabelText(/invite member/i), 'charlie')
    await user.click(screen.getByRole('button', { name: /invite/i }))

    expect(mutate).toHaveBeenCalledOnce()
    expect(mutate).toHaveBeenCalledWith(
      { username: 'charlie' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it('does not submit when username is empty', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseAddMember.mockReturnValue(addMutationResult({ mutate }))

    renderForm('owner')

    // Invite button should be disabled when input is empty
    const button = screen.getByRole('button', { name: /invite/i })
    expect(button).toBeDisabled()

    await user.click(button)
    expect(mutate).not.toHaveBeenCalled()
  })

  it('shows conflict (409) error message when mutation errors', () => {
    mockedUseAddMember.mockReturnValue(
      addMutationResult({
        isError: true,
        error: new Error('User is already a member.'),
      }),
    )

    renderForm('owner')

    expect(screen.getByRole('alert')).toHaveTextContent('User is already a member.')
  })

  it('shows generic error message on other failures', () => {
    mockedUseAddMember.mockReturnValue(
      addMutationResult({
        isError: true,
        error: new Error('Failed to add member'),
      }),
    )

    renderForm('owner')

    expect(screen.getByRole('alert')).toHaveTextContent('Failed to add member')
  })
})
