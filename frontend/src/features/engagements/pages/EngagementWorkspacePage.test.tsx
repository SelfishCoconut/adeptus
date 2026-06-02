import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { EngagementWorkspacePage } from './EngagementWorkspacePage'
import { useMe, useLogout } from '@/features/auth/api'
import { useEngagement, useMembers, useRemoveMember, useAddMember, useUpdateEngagement } from '../api'
import type { PrivacyMode } from '@/shared/api'

// Mock the hooks the page uses
vi.mock('@/features/auth/api', () => ({
  useMe: vi.fn(),
  useLogout: vi.fn(),
}))

vi.mock('../api', () => ({
  useEngagement: vi.fn(),
  useMembers: vi.fn(),
  useRemoveMember: vi.fn(),
  useAddMember: vi.fn(),
  useUpdateEngagement: vi.fn(),
}))

// Mock the child components that rely on further network calls
vi.mock('@/features/auth/components/TermsGate', () => ({
  TermsGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/features/workspace/WorkspaceShell', () => ({
  WorkspaceShell: ({
    username,
    role,
    onLogout,
    isLoggingOut,
    privacyMode,
    engagementId,
  }: {
    username: string
    role: string
    onLogout: () => void
    isLoggingOut: boolean
    privacyMode: PrivacyMode
    engagementId?: string
  }) => (
    <div
      data-testid="workspace-shell"
      data-logging-out={String(isLoggingOut)}
      data-privacy-mode={privacyMode}
      data-engagement-id={engagementId ?? ''}
    >
      <span data-testid="username">{username}</span>
      <span data-testid="role">{role}</span>
      <button type="button" onClick={onLogout}>
        Logout
      </button>
    </div>
  ),
}))

const mockedUseMe = vi.mocked(useMe)
const mockedUseLogout = vi.mocked(useLogout)
const mockedUseEngagement = vi.mocked(useEngagement)
const mockedUseMembers = vi.mocked(useMembers)
const mockedUseRemoveMember = vi.mocked(useRemoveMember)
const mockedUseAddMember = vi.mocked(useAddMember)
const mockedUseUpdateEngagement = vi.mocked(useUpdateEngagement)

const ADMIN_USER = {
  id: '00000000-0000-0000-0000-000000000001',
  username: 'admin',
  role: 'admin' as const,
  terms_accepted_at: '2026-01-01T00:00:00Z',
}

const ENGAGEMENT_ID = 'aaaaaaaa-0000-0000-0000-000000000001'

function logoutMutation(overrides: {
  mutate?: ReturnType<typeof useLogout>['mutate']
  isPending?: boolean
} = {}) {
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
  } as unknown as ReturnType<typeof useLogout>
}

function updateMutation(overrides: {
  mutate?: ReturnType<typeof useUpdateEngagement>['mutate']
  isPending?: boolean
} = {}) {
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
  } as unknown as ReturnType<typeof useUpdateEngagement>
}

function renderPage(engagementId = ENGAGEMENT_ID) {
  return render(
    <MemoryRouter initialEntries={[`/engagements/${engagementId}/workspace`]}>
      <Routes>
        <Route path="/engagements/:id/workspace" element={<EngagementWorkspacePage />} />
        <Route path="/login" element={<div data-testid="login-page" />} />
      </Routes>
    </MemoryRouter>,
  )
}

// Default stub for membership hooks — all tests that don't focus on membership
// use these so MembersList and InviteMemberForm render without network calls.
function setupMembershipDefaults(memberRole: 'owner' | 'member' = 'owner') {
  mockedUseEngagement.mockReturnValue({
    data: {
      id: ENGAGEMENT_ID,
      name: 'Test Engagement',
      status: 'active' as const,
      scope: '10.0.0.0/8',
      client_info: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      member_role: memberRole,
      privacy_mode: 'local_only' as const,
    },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useEngagement>)

  mockedUseMembers.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useMembers>)

  mockedUseRemoveMember.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
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
  } as unknown as ReturnType<typeof useRemoveMember>)

  mockedUseAddMember.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
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
  } as unknown as ReturnType<typeof useAddMember>)

  mockedUseUpdateEngagement.mockReturnValue(updateMutation())
}

beforeEach(() => {
  mockedUseMe.mockReset()
  mockedUseLogout.mockReset()
  mockedUseEngagement.mockReset()
  mockedUseMembers.mockReset()
  mockedUseRemoveMember.mockReset()
  mockedUseAddMember.mockReset()
  mockedUseUpdateEngagement.mockReset()
  setupMembershipDefaults()
})

describe('EngagementWorkspacePage', () => {
  it('renders null when user data is not yet available (loading state)', () => {
    mockedUseMe.mockReturnValue({
      data: null,
      isLoading: true,
      isSuccess: false,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())

    const { container } = renderPage()

    expect(container.firstChild).toBeNull()
  })

  it('renders the WorkspaceShell when the user is authenticated', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())

    renderPage()

    expect(screen.getByTestId('workspace-shell')).toBeInTheDocument()
    expect(screen.getByTestId('username')).toHaveTextContent('admin')
    expect(screen.getByTestId('role')).toHaveTextContent('admin')
  })

  it('passes isLoggingOut=true to WorkspaceShell while logout is pending', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation({ isPending: true }))

    renderPage()

    expect(screen.getByTestId('workspace-shell')).toHaveAttribute('data-logging-out', 'true')
  })

  it('calls logout.mutate and navigates to /login on logout', async () => {
    const user = userEvent.setup()

    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)

    const mutate = vi.fn((_: undefined, options?: Record<string, unknown>) => {
      const cb = options?.['onSuccess']
      if (typeof cb === 'function') cb()
    }) as unknown as ReturnType<typeof useLogout>['mutate']

    mockedUseLogout.mockReturnValue(logoutMutation({ mutate }))

    renderPage()

    await user.click(screen.getByRole('button', { name: /logout/i }))

    expect(mutate).toHaveBeenCalledOnce()
    // After onSuccess fires the router navigates to /login
    expect(screen.getByTestId('login-page')).toBeInTheDocument()
  })

  it('renders the workspace for a different engagement id', () => {
    const otherId = 'bbbbbbbb-0000-0000-0000-000000000002'
    mockedUseMe.mockReturnValue({
      data: { ...ADMIN_USER, username: 'alice', role: 'user' as const },
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())

    renderPage(otherId)

    expect(screen.getByTestId('workspace-shell')).toBeInTheDocument()
    expect(screen.getByTestId('username')).toHaveTextContent('alice')
  })

  it('renders the membership panel with MembersList and InviteMemberForm (W-03)', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())
    // useEngagement already set up with member_role='owner' in setupMembershipDefaults.
    // useMembers returns an empty list — MembersList will render "No members yet."

    renderPage()

    // Membership section heading
    expect(screen.getByRole('heading', { name: /members/i })).toBeInTheDocument()
    // InviteMemberForm is rendered for owners
    expect(screen.getByLabelText(/invite member/i)).toBeInTheDocument()
    // MembersList renders its empty state
    expect(screen.getByText('No members yet.')).toBeInTheDocument()
  })

  it('shows MembersList with member data when members are loaded', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())

    mockedUseMembers.mockReturnValue({
      data: [
        {
          user_id: '00000000-0000-0000-0000-000000000010',
          username: 'alice',
          role: 'owner' as const,
          joined_at: '2026-01-01T00:00:00Z',
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useMembers>)

    renderPage()

    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('passes privacyMode to WorkspaceShell from engagement data', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())
    mockedUseEngagement.mockReturnValue({
      data: {
        id: ENGAGEMENT_ID,
        name: 'Test Engagement',
        status: 'active' as const,
        scope: '10.0.0.0/8',
        client_info: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        member_role: 'owner' as const,
        privacy_mode: 'cloud_enabled' as const,
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useEngagement>)

    renderPage()

    expect(screen.getByTestId('workspace-shell')).toHaveAttribute(
      'data-privacy-mode',
      'cloud_enabled',
    )
  })

  it('passes local_only as safe default during loading', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())
    mockedUseEngagement.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useEngagement>)

    renderPage()

    expect(screen.getByTestId('workspace-shell')).toHaveAttribute(
      'data-privacy-mode',
      'local_only',
    )
  })

  it('passes engagementId to WorkspaceShell so the Console pane can render the ToolRunnerPanel', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())

    renderPage(ENGAGEMENT_ID)

    expect(screen.getByTestId('workspace-shell')).toHaveAttribute(
      'data-engagement-id',
      ENGAGEMENT_ID,
    )
  })

  it('owner sees inline toggle in workspace', () => {
    mockedUseMe.mockReturnValue({
      data: ADMIN_USER,
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())
    // setupMembershipDefaults already sets member_role: 'owner'

    renderPage()

    expect(screen.getByRole('switch')).toBeInTheDocument()
    expect(screen.getByLabelText(/enable cloud llm/i)).toBeInTheDocument()
  })

  it('non-owner cannot see inline toggle', () => {
    mockedUseMe.mockReturnValue({
      data: { ...ADMIN_USER, role: 'user' as const },
      isLoading: false,
      isSuccess: true,
      isError: false,
    } as unknown as ReturnType<typeof useMe>)
    mockedUseLogout.mockReturnValue(logoutMutation())
    setupMembershipDefaults('member')

    renderPage()

    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
