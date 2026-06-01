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

vi.mock('@/features/mcp/components/RawShellForm', () => ({
  RawShellForm: () => <div data-testid="raw-shell-form" />,
}))

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

  describe('Console pane — RawShellForm visibility', () => {
    it('shows the RawShellForm when an engagementId is provided', () => {
      render(
        <WorkspaceShell
          username="alice"
          role="admin"
          onLogout={vi.fn()}
          privacyMode="local_only"
          engagementId="aaaaaaaa-0000-0000-0000-000000000001"
        />,
      )

      expect(screen.getByTestId('raw-shell-form')).toBeInTheDocument()
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

      expect(screen.queryByTestId('raw-shell-form')).not.toBeInTheDocument()
      expect(
        screen.getByText(/select an engagement to use the shell runner/i),
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

      expect(screen.queryByTestId('raw-shell-form')).not.toBeInTheDocument()
      expect(
        screen.getByText(/select an engagement to use the shell runner/i),
      ).toBeInTheDocument()
    })
  })
})
