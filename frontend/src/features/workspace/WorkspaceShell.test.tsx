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

describe('WorkspaceShell', () => {
  it('renders the top bar (username, role, logout, health) and three panes', () => {
    render(<WorkspaceShell username="alice" role="admin" onLogout={vi.fn()} />)

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
    render(<WorkspaceShell username="alice" role="admin" onLogout={onLogout} />)

    await user.click(screen.getByRole('button', { name: /logout/i }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })
})
