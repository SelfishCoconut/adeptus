import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { EgressConfirmModal } from './EgressConfirmModal'

describe('EgressConfirmModal', () => {
  it('renders the human-readable labels for the matched categories', () => {
    render(
      <EgressConfirmModal
        open={true}
        categories={['aws_access_key', 'password_assignment']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByText(/AWS access key/)).toBeInTheDocument()
    expect(screen.getByText(/password= assignment/)).toBeInTheDocument()
    // The copy makes the no-redaction guarantee explicit (§5.5).
    expect(screen.getByText(/unmodified/)).toBeInTheDocument()
  })

  it('calls onConfirm when "Send anyway" is clicked', async () => {
    const onConfirm = vi.fn()
    render(
      <EgressConfirmModal
        open={true}
        categories={['aws_access_key']}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /send anyway/i }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel when "Cancel" is clicked', async () => {
    const onCancel = vi.fn()
    render(
      <EgressConfirmModal
        open={true}
        categories={['aws_access_key']}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('never renders the message content or a secret value (it only receives category names)', () => {
    // The modal has no content/value prop by design; assert a secret-shaped string the modal
    // is never given cannot appear (§5.5 / Risk 7).
    render(
      <EgressConfirmModal
        open={true}
        categories={['aws_access_key']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.queryByText(/AKIAIOSFODNN7EXAMPLE/)).not.toBeInTheDocument() // gitleaks:allow
  })

  it('treats closing via Escape as a cancel (no send)', async () => {
    const onCancel = vi.fn()
    const onConfirm = vi.fn()
    render(
      <EgressConfirmModal
        open={true}
        categories={['aws_access_key']}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    )
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('does not render when closed', () => {
    render(
      <EgressConfirmModal
        open={false}
        categories={['aws_access_key']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.queryByText(/may contain a secret/i)).not.toBeInTheDocument()
  })
})
