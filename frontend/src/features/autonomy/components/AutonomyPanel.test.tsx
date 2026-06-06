import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AutonomyPanel } from './AutonomyPanel'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockDelete = vi.mocked(api.DELETE)

const ENG = 'eng-1'

const grant = (overrides: Record<string, unknown> = {}) => ({
  id: 'grant-1',
  engagement_id: ENG,
  reason: 'aggressive_scan',
  granted_by_user_id: 'user-1',
  granted_by_username: 'pentester',
  created_at: '2026-06-06T12:00:00Z',
  revoked_at: null,
  ...overrides,
})

function renderPanel(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  mockGet.mockReset()
  mockDelete.mockReset()
})

describe('AutonomyPanel', () => {
  it('shows the empty state when there are no grants', async () => {
    mockGet.mockResolvedValue({ data: [] } as never)
    renderPanel(<AutonomyPanel engagementId={ENG} />)
    await waitFor(() => expect(screen.getByTestId('autonomy-empty')).toBeInTheDocument())
  })

  it('lists active grants with category, grantor, and date', async () => {
    mockGet.mockResolvedValue({
      data: [grant(), grant({ id: 'grant-2', reason: 'out_of_scope', granted_by_username: 'lead' })],
    } as never)
    renderPanel(<AutonomyPanel engagementId={ENG} />)
    await waitFor(() => expect(screen.getAllByTestId('autonomy-grant')).toHaveLength(2))
    expect(screen.getByText('Aggressive scans')).toBeInTheDocument()
    expect(screen.getByText('Out-of-scope commands')).toBeInTheDocument()
    expect(screen.getByText(/granted by @pentester/)).toHaveTextContent('2026-06-06')
  })

  it('falls back to the raw reason value for an unknown category', async () => {
    mockGet.mockResolvedValue({ data: [grant({ reason: 'mystery' })] } as never)
    renderPanel(<AutonomyPanel engagementId={ENG} />)
    await waitFor(() => expect(screen.getByText('mystery')).toBeInTheDocument())
  })

  it('revokes a grant via the delete endpoint', async () => {
    mockGet.mockResolvedValue({ data: [grant()] } as never)
    mockDelete.mockResolvedValue({ data: undefined } as never)
    renderPanel(<AutonomyPanel engagementId={ENG} />)
    await waitFor(() => expect(screen.getByTestId('revoke-grant-1')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('revoke-grant-1'))
    await waitFor(() => expect(mockDelete).toHaveBeenCalled())
    const call = mockDelete.mock.calls[0][1] as { params: { path: Record<string, unknown> } }
    expect(call.params.path.grant_id).toBe('grant-1')
  })

  it('shows an error state when the grants query fails', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'nope' }, response: { status: 404 } } as never)
    renderPanel(<AutonomyPanel engagementId={ENG} />)
    await waitFor(() => expect(screen.getByText('Failed to load grants.')).toBeInTheDocument())
  })
})
