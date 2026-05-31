import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { EngagementsPage } from './EngagementsPage'

// Mock child components to keep the page test focused on orchestration.
vi.mock('../components/EngagementList', () => ({
  EngagementList: () => <div data-testid="engagement-list" />,
}))

vi.mock('../components/NewEngagementDialog', () => ({
  NewEngagementDialog: ({
    open,
    onOpenChange,
  }: {
    open: boolean
    onOpenChange: (v: boolean) => void
  }) => (
    <div data-testid="new-engagement-dialog" data-open={String(open)}>
      <button type="button" onClick={() => onOpenChange(false)}>
        Close
      </button>
    </div>
  ),
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <EngagementsPage />
    </MemoryRouter>,
  )
}

describe('EngagementsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the Adeptus heading', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'Adeptus' })).toBeInTheDocument()
  })

  it('renders the New Engagement button', () => {
    renderPage()
    expect(screen.getByRole('button', { name: /new engagement/i })).toBeInTheDocument()
  })

  it('renders the EngagementList', () => {
    renderPage()
    expect(screen.getByTestId('engagement-list')).toBeInTheDocument()
  })

  it('dialog is closed by default', () => {
    renderPage()
    expect(screen.getByTestId('new-engagement-dialog')).toHaveAttribute('data-open', 'false')
  })

  it('opens the dialog when New Engagement is clicked', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /new engagement/i }))

    expect(screen.getByTestId('new-engagement-dialog')).toHaveAttribute('data-open', 'true')
  })

  it('closes the dialog when onOpenChange(false) is called', async () => {
    const user = userEvent.setup()
    renderPage()

    // Open it first
    await user.click(screen.getByRole('button', { name: /new engagement/i }))
    expect(screen.getByTestId('new-engagement-dialog')).toHaveAttribute('data-open', 'true')

    // Close via dialog's close button
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.getByTestId('new-engagement-dialog')).toHaveAttribute('data-open', 'false')
  })
})
