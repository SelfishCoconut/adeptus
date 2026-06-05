import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { PlanStep } from '@/shared/api'
import { PlanPanel } from './PlanPanel'

describe('PlanPanel', () => {
  it('renders the steps in order with their status', () => {
    const plan: PlanStep[] = [
      { step: 'Enumerate the login endpoint', status: 'done' },
      { step: 'Test SQLi on the username field', status: 'in_progress' },
      { step: 'Check session-cookie flags', status: 'todo' },
    ]
    render(<PlanPanel plan={plan} />)

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Enumerate the login endpoint')
    expect(items[0]).toHaveAttribute('data-status', 'done')
    expect(items[1]).toHaveAttribute('data-status', 'in_progress')
    expect(items[2]).toHaveAttribute('data-status', 'todo')
  })

  it('renders step text verbatim (no redaction, §5.5)', () => {
    const plan: PlanStep[] = [
      { step: 'Note the db password hunter2 in the report', status: 'todo' },
    ]
    render(<PlanPanel plan={plan} />)
    expect(
      screen.getByText('Note the db password hunter2 in the report'),
    ).toBeInTheDocument()
  })

  it('exposes an accessible status label per step', () => {
    render(<PlanPanel plan={[{ step: 'Try default creds', status: 'in_progress' }]} />)
    expect(screen.getByText('In progress')).toBeInTheDocument()
  })

  it('shows a subtle no-plan note when the plan is empty', () => {
    render(<PlanPanel plan={[]} />)
    expect(screen.getByTestId('plan-panel-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('plan-panel')).not.toBeInTheDocument()
  })
})
