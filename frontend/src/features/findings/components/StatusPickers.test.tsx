import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StatusPickers } from './StatusPickers'
import { useSetRemediation, useSetVerification } from '../api'
import type { Finding } from '../api'

vi.mock('../api', () => ({
  useSetVerification: vi.fn(),
  useSetRemediation: vi.fn(),
}))

const mockedUseSetVerification = vi.mocked(useSetVerification)
const mockedUseSetRemediation = vi.mocked(useSetRemediation)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 'finding-1',
    engagement_id: ENGAGEMENT_ID,
    title: 'Reflected XSS',
    description: '',
    severity: 'high',
    verification_status: 'unverified',
    remediation_status: 'open',
    node_id: null,
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function mutationResult<T = ReturnType<typeof useSetVerification>>(
  overrides: Record<string, unknown> = {},
): T {
  return {
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    variables: undefined,
    ...overrides,
  } as unknown as T
}

describe('StatusPickers', () => {
  beforeEach(() => {
    mockedUseSetVerification.mockReset()
    mockedUseSetRemediation.mockReset()
    mockedUseSetVerification.mockReturnValue(mutationResult())
    mockedUseSetRemediation.mockReturnValue(
      mutationResult<ReturnType<typeof useSetRemediation>>(),
    )
  })

  it('shows the current server statuses as the selected values', () => {
    render(
      <StatusPickers
        engagementId={ENGAGEMENT_ID}
        finding={makeFinding({ verification_status: 'verified', remediation_status: 'fixed' })}
      />,
    )
    expect(screen.getByLabelText('Verification status')).toHaveValue('verified')
    expect(screen.getByLabelText('Remediation status')).toHaveValue('fixed')
  })

  it('fires the verification mutation when changed', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseSetVerification.mockReturnValue(mutationResult({ mutate }))

    render(<StatusPickers engagementId={ENGAGEMENT_ID} finding={makeFinding()} />)
    await user.selectOptions(screen.getByLabelText('Verification status'), 'false_positive')

    expect(mutate).toHaveBeenCalledWith({
      findingId: 'finding-1',
      verification_status: 'false_positive',
    })
  })

  it('fires the remediation mutation when changed', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseSetRemediation.mockReturnValue(
      mutationResult<ReturnType<typeof useSetRemediation>>({ mutate }),
    )

    render(<StatusPickers engagementId={ENGAGEMENT_ID} finding={makeFinding()} />)
    await user.selectOptions(screen.getByLabelText('Remediation status'), 'risk_accepted')

    expect(mutate).toHaveBeenCalledWith({
      findingId: 'finding-1',
      remediation_status: 'risk_accepted',
    })
  })

  it('reverts to the server value and shows an alert on error', () => {
    mockedUseSetVerification.mockReturnValue(
      mutationResult({ isError: true, error: new Error('Conflict: archived') }),
    )

    render(
      <StatusPickers
        engagementId={ENGAGEMENT_ID}
        finding={makeFinding({ verification_status: 'unverified' })}
      />,
    )

    // The control still reflects the server value (not the failed selection).
    expect(screen.getByLabelText('Verification status')).toHaveValue('unverified')
    expect(screen.getByRole('alert')).toHaveTextContent('Conflict: archived')
  })

  it('shows the optimistic value while a verification change is pending', () => {
    mockedUseSetVerification.mockReturnValue(
      mutationResult({
        isPending: true,
        variables: { findingId: 'finding-1', verification_status: 'verified' },
      }),
    )

    render(
      <StatusPickers
        engagementId={ENGAGEMENT_ID}
        finding={makeFinding({ verification_status: 'unverified' })}
      />,
    )
    // Optimistic: shows the in-flight value, not the stale server value.
    expect(screen.getByLabelText('Verification status')).toHaveValue('verified')
  })
})
