import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TermsGate } from './TermsGate'
import { useAcceptTerms, useMe } from '../api'

vi.mock('../api', () => ({
  useMe: vi.fn(),
  useAcceptTerms: vi.fn(),
}))

const mockedUseMe = vi.mocked(useMe)
const mockedUseAcceptTerms = vi.mocked(useAcceptTerms)

const baseUser = {
  id: '00000000-0000-0000-0000-000000000001',
  username: 'admin',
  role: 'admin' as const,
}

function meResult(termsAcceptedAt: string | null) {
  return {
    data: { ...baseUser, terms_accepted_at: termsAcceptedAt },
  } as unknown as ReturnType<typeof useMe>
}

function acceptResult(overrides: Partial<ReturnType<typeof useAcceptTerms>>) {
  return {
    mutate: vi.fn(),
    isPending: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useAcceptTerms>
}

describe('TermsGate', () => {
  beforeEach(() => {
    mockedUseMe.mockReset()
    mockedUseAcceptTerms.mockReset()
  })

  it('shows the gate and hides children when terms are not accepted', () => {
    mockedUseMe.mockReturnValue(meResult(null))
    mockedUseAcceptTerms.mockReturnValue(acceptResult({}))
    render(
      <TermsGate>
        <div>workspace content</div>
      </TermsGate>,
    )

    expect(screen.getByText(/terms of use/i)).toBeInTheDocument()
    expect(screen.queryByText('workspace content')).not.toBeInTheDocument()
  })

  it('calls accept and reveals children once terms are accepted', async () => {
    const user = userEvent.setup()
    let accepted = false
    const mutate = vi.fn(() => {
      accepted = true
    })
    mockedUseMe.mockImplementation(() => meResult(accepted ? '2026-01-01T00:00:00Z' : null))
    mockedUseAcceptTerms.mockReturnValue(acceptResult({ mutate }))

    const { rerender } = render(
      <TermsGate>
        <div>workspace content</div>
      </TermsGate>,
    )

    expect(screen.getByText(/terms of use/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /accept/i }))
    expect(mutate).toHaveBeenCalledTimes(1)

    // Fresh element so React re-renders (it bails out on referential equality).
    rerender(
      <TermsGate>
        <div>workspace content</div>
      </TermsGate>,
    )
    expect(screen.getByText('workspace content')).toBeInTheDocument()
    expect(screen.queryByText(/terms of use/i)).not.toBeInTheDocument()
  })
})
