import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkspaceShell } from './WorkspaceShell'

vi.mock('./HealthIndicator', () => ({
  HealthIndicator: () => <div data-testid="health-indicator" />,
}))

vi.mock('@/components/theme/ModeToggle', () => ({
  ModeToggle: () => <button type="button">Toggle theme</button>,
}))

vi.mock('./components/PrivacyModeBanner', () => ({
  PrivacyModeBanner: ({ privacyMode }: { privacyMode: string }) => (
    <div data-testid="privacy-mode-banner" data-privacy-mode={privacyMode} />
  ),
}))

vi.mock('@/features/mcp/components/ToolRunnerPanel', () => ({
  ToolRunnerPanel: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="tool-runner-panel" data-engagement-id={engagementId} />
  ),
}))

vi.mock('@/features/graph/components', () => ({
  GraphPane: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="graph-pane" data-engagement-id={engagementId} />
  ),
}))

vi.mock('@/features/audit/components/AuditPanel', () => ({
  AuditPanel: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="audit-panel" data-engagement-id={engagementId} />
  ),
}))

vi.mock('@/features/approvals/components/ApprovalQueue', () => ({
  ApprovalQueue: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="approval-queue" data-engagement-id={engagementId} />
  ),
}))

vi.mock('@/features/chat/components/ChatPanel', () => ({
  ChatPanel: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="chat-panel" data-engagement-id={engagementId} />
  ),
}))

// The certainty overlay hooks run a chat query; stub them so the shell needs no QueryClient.
vi.mock('@/features/chat/hooks/useCertaintyByNode', () => ({
  useCertaintyByNode: () => new Map<string, number>(),
}))

vi.mock('@/features/chat/hooks/useLowConfidenceThreshold', () => ({
  useLowConfidenceThreshold: () => 70,
}))

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

describe('WorkspaceShell', () => {
  it('renders the top bar (username, role, logout, health) and three panes', () => {
    render(
      <WorkspaceShell
        username="alice"
        role="admin"
        onLogout={vi.fn()}
        privacyMode="local_only"
      />,
    )

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /logout/i })).toBeInTheDocument()
    expect(screen.getByTestId('health-indicator')).toBeInTheDocument()

    expect(screen.getByRole('region', { name: /ai chat/i })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /graph/i })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /console/i })).toBeInTheDocument()
  })

  it('calls onLogout when the logout button is clicked', async () => {
    const user = userEvent.setup()
    const onLogout = vi.fn()
    render(
      <WorkspaceShell
        username="alice"
        role="admin"
        onLogout={onLogout}
        privacyMode="local_only"
      />,
    )

    await user.click(screen.getByRole('button', { name: /logout/i }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })

  it('renders banner with local_only privacyMode', () => {
    render(
      <WorkspaceShell
        username="alice"
        role="admin"
        onLogout={vi.fn()}
        privacyMode="local_only"
      />,
    )

    const banner = screen.getByTestId('privacy-mode-banner')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveAttribute('data-privacy-mode', 'local_only')
  })

  it('renders banner with cloud_enabled privacyMode', () => {
    render(
      <WorkspaceShell
        username="alice"
        role="admin"
        onLogout={vi.fn()}
        privacyMode="cloud_enabled"
      />,
    )

    const banner = screen.getByTestId('privacy-mode-banner')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveAttribute('data-privacy-mode', 'cloud_enabled')
  })

  describe('AI chat pane — ChatPanel visibility', () => {
    it('renders the ChatPanel for the engagement, with the privacy banner still pinned', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId={ENGAGEMENT_ID}
        />,
      )

      const panel = screen.getByTestId('chat-panel')
      expect(panel).toBeInTheDocument()
      expect(panel).toHaveAttribute('data-engagement-id', ENGAGEMENT_ID)
      // §5.5: the Slice-02 privacy banner stays visible above the panes.
      expect(screen.getByTestId('privacy-mode-banner')).toBeInTheDocument()
    })

    it('shows the "select an engagement" placeholder when no engagementId is provided', () => {
      render(
        <WorkspaceShell username="alice" role="admin" onLogout={vi.fn()} privacyMode="local_only" />,
      )

      expect(screen.queryByTestId('chat-panel')).not.toBeInTheDocument()
      expect(screen.getByText(/select an engagement to chat with the ai/i)).toBeInTheDocument()
    })
  })

  describe('Graph pane — GraphPane visibility', () => {
    it('shows GraphPane for the engagement when an engagementId is provided', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId="aaaaaaaa-0000-0000-0000-000000000001"
        />,
      )

      const pane = screen.getByTestId('graph-pane')
      expect(pane).toBeInTheDocument()
      expect(pane).toHaveAttribute('data-engagement-id', 'aaaaaaaa-0000-0000-0000-000000000001')
      expect(
        screen.queryByText(/select an engagement to view the graph/i),
      ).not.toBeInTheDocument()
    })

    it('shows the "select an engagement" placeholder when no engagementId is provided', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
        />,
      )

      expect(screen.queryByTestId('graph-pane')).not.toBeInTheDocument()
      expect(
        screen.getByText(/select an engagement to view the graph/i),
      ).toBeInTheDocument()
    })
  })

  describe('Console pane — ToolRunnerPanel visibility', () => {
    it('shows the ToolRunnerPanel for the engagement when an engagementId is provided', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId="aaaaaaaa-0000-0000-0000-000000000001"
        />,
      )

      const panel = screen.getByTestId('tool-runner-panel')
      expect(panel).toBeInTheDocument()
      expect(panel).toHaveAttribute('data-engagement-id', 'aaaaaaaa-0000-0000-0000-000000000001')
      expect(
        screen.queryByText(/select an engagement/i),
      ).not.toBeInTheDocument()
    })

    it('shows the "select an engagement" placeholder when no engagementId is provided', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
        />,
      )

      expect(screen.queryByTestId('tool-runner-panel')).not.toBeInTheDocument()
      expect(
        screen.getByText(/select an engagement to use the tool runner/i),
      ).toBeInTheDocument()
    })

    it('shows the "select an engagement" placeholder when engagementId is empty string', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId=""
        />,
      )

      expect(screen.queryByTestId('tool-runner-panel')).not.toBeInTheDocument()
      expect(
        screen.getByText(/select an engagement to use the tool runner/i),
      ).toBeInTheDocument()
    })
  })

  describe('audit panel (admin-gated, §14)', () => {
    it('renders the audit panel for an admin with an open engagement', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId={ENGAGEMENT_ID}
        />,
      )
      const panel = screen.getByTestId('audit-panel')
      expect(panel).toBeInTheDocument()
      expect(panel).toHaveAttribute('data-engagement-id', ENGAGEMENT_ID)
    })

    it('hides the audit panel for a non-admin member', () => {
      render(
        <WorkspaceShell
          username="bob"
          role="user"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId={ENGAGEMENT_ID}
        />,
      )
      expect(screen.queryByTestId('audit-panel')).not.toBeInTheDocument()
      // The approval queue is visible to ALL members, not just admins (§5.2).
      expect(screen.getByTestId('approval-queue')).toBeInTheDocument()
    })

    it('hides the audit panel when no engagement is open (even for an admin)', () => {
      render(
        <WorkspaceShell username="alice" role="admin" onLogout={vi.fn()} privacyMode="local_only" />,
      )
      expect(screen.queryByTestId('audit-panel')).not.toBeInTheDocument()
    })
  })
})
