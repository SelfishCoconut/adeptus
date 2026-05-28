import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HealthIndicator } from './HealthIndicator'
import { useHealth } from './api'

vi.mock('./api', () => ({
  useHealth: vi.fn(),
}))

const mockedUseHealth = vi.mocked(useHealth)

function healthResult(overrides: { isSuccess?: boolean; isError?: boolean }) {
  return {
    isSuccess: false,
    isError: false,
    ...overrides,
  } as unknown as ReturnType<typeof useHealth>
}

describe('HealthIndicator', () => {
  beforeEach(() => {
    mockedUseHealth.mockReset()
  })

  it('shows a green/reachable dot when the backend is healthy', () => {
    mockedUseHealth.mockReturnValue(healthResult({ isSuccess: true }))
    render(<HealthIndicator />)

    expect(screen.getByLabelText(/backend reachable/i)).toBeInTheDocument()
    expect(screen.getByText(/connected/i)).toBeInTheDocument()
  })

  it('shows a red/unreachable dot when the health query errors', () => {
    mockedUseHealth.mockReturnValue(healthResult({ isError: true }))
    render(<HealthIndicator />)

    expect(screen.getByLabelText(/backend unreachable/i)).toBeInTheDocument()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })
})
