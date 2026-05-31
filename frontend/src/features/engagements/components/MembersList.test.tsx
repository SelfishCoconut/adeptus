import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MembersList } from './MembersList'
import { useMembers, useRemoveMember } from '../api'

vi.mock('../api', () => ({
  useMembers: vi.fn(),
  useRemoveMember: vi.fn(),
}))

const mockedUseMembers = vi.mocked(useMembers)
const mockedUseRemoveMember = vi.mocked(useRemoveMember)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function membersResult(overrides: Partial<ReturnType<typeof useMembers>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useMembers>
}

function removeMutationResult(overrides: { mutate?: ReturnType<typeof useRemoveMember>['mutate']; isPending?: boolean } = {}) {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    isPending: overrides.isPending ?? false,
    isError: false,
    error: null,
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
  } as unknown as ReturnType<typeof useRemoveMember>
}

const SAMPLE_MEMBERS = [
  {
    user_id: '00000000-0000-0000-0000-000000000010',
    username: 'alice',
    role: 'owner' as const,
    joined_at: '2026-01-01T00:00:00Z',
  },
  {
    user_id: '00000000-0000-0000-0000-000000000011',
    username: 'bob',
    role: 'member' as const,
    joined_at: '2026-01-02T00:00:00Z',
  },
]

function renderMembersList(callerRole: 'owner' | 'member' = 'member') {
  return render(
    <MembersList engagementId={ENGAGEMENT_ID} callerRole={callerRole} />,
  )
}

describe('MembersList', () => {
  beforeEach(() => {
    mockedUseMembers.mockReset()
    mockedUseRemoveMember.mockReset()
    mockedUseRemoveMember.mockReturnValue(removeMutationResult())
  })

  it('renders member usernames and role badges', () => {
    mockedUseMembers.mockReturnValue(membersResult({ data: SAMPLE_MEMBERS }))

    renderMembersList()

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.getByText('owner')).toBeInTheDocument()
    expect(screen.getByText('member')).toBeInTheDocument()
  })

  it('shows skeleton while loading', () => {
    mockedUseMembers.mockReturnValue(membersResult({ isLoading: true }))

    renderMembersList()

    expect(screen.getByTestId('members-list-skeleton')).toBeInTheDocument()
  })

  it('owner sees Remove button for non-owner members', () => {
    mockedUseMembers.mockReturnValue(membersResult({ data: SAMPLE_MEMBERS }))

    renderMembersList('owner')

    // alice is owner — no Remove button for her; bob is member — Remove button shown
    expect(screen.getByRole('button', { name: /remove/i })).toBeInTheDocument()
  })

  it('owner does NOT see Remove button for the owner member', () => {
    mockedUseMembers.mockReturnValue(membersResult({ data: SAMPLE_MEMBERS }))

    renderMembersList('owner')

    // Only one Remove button — for bob, not alice (owner cannot remove owner)
    expect(screen.getAllByRole('button', { name: /remove/i })).toHaveLength(1)
  })

  it('non-owner (member role) sees no Remove buttons', () => {
    mockedUseMembers.mockReturnValue(membersResult({ data: SAMPLE_MEMBERS }))

    renderMembersList('member')

    expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument()
  })

  it('shows error message when query errors with an Error instance', () => {
    mockedUseMembers.mockReturnValue(
      membersResult({ isError: true, error: new Error('Network timeout') }),
    )

    renderMembersList()

    expect(screen.getByRole('alert')).toHaveTextContent('Network timeout')
  })

  it('shows fallback error message when error is not an Error instance', () => {
    mockedUseMembers.mockReturnValue(
      membersResult({ isError: true, error: 'unexpected string' as unknown as Error }),
    )

    renderMembersList()

    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load members.')
  })

  it('shows empty state when there are no members', () => {
    mockedUseMembers.mockReturnValue(membersResult({ data: [] }))

    renderMembersList()

    expect(screen.getByText('No members yet.')).toBeInTheDocument()
  })

  it('calls removeMember mutation when Remove is clicked', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseMembers.mockReturnValue(membersResult({ data: SAMPLE_MEMBERS }))
    mockedUseRemoveMember.mockReturnValue(removeMutationResult({ mutate }))

    renderMembersList('owner')

    await user.click(screen.getByRole('button', { name: /remove/i }))

    expect(mutate).toHaveBeenCalledOnce()
    expect(mutate).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000011')
  })
})
