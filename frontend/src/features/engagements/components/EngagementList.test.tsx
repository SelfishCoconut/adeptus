import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { EngagementList } from './EngagementList'
import { useEngagements } from '../api'

vi.mock('../api', () => ({
  useEngagements: vi.fn(),
}))

const mockedUseEngagements = vi.mocked(useEngagements)

function listResult(overrides: Partial<ReturnType<typeof useEngagements>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useEngagements>
}

function renderList() {
  return render(
    <MemoryRouter>
      <EngagementList />
    </MemoryRouter>,
  )
}

describe('EngagementList', () => {
  beforeEach(() => {
    mockedUseEngagements.mockReset()
  })

  it('renders cards from mock data', () => {
    mockedUseEngagements.mockReturnValue(
      listResult({
        data: [
          {
            id: '00000000-0000-0000-0000-000000000001',
            name: 'Alpha Pentest',
            status: 'active',
            created_at: '2026-01-01T00:00:00Z',
            member_role: 'owner',
          },
          {
            id: '00000000-0000-0000-0000-000000000002',
            name: 'Beta Audit',
            status: 'archived',
            created_at: '2026-02-01T00:00:00Z',
            member_role: 'member',
          },
        ],
      }),
    )

    renderList()

    expect(screen.getByText('Alpha Pentest')).toBeInTheDocument()
    expect(screen.getByText('Beta Audit')).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
    expect(screen.getByText('archived')).toBeInTheDocument()
    expect(screen.getByText('owner')).toBeInTheDocument()
    expect(screen.getByText('member')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Open' })).toHaveLength(2)
  })

  it('renders empty state', () => {
    mockedUseEngagements.mockReturnValue(listResult({ data: [] }))

    renderList()

    expect(screen.getByText('No engagements — create one.')).toBeInTheDocument()
  })

  it('shows skeleton while loading', () => {
    mockedUseEngagements.mockReturnValue(listResult({ isLoading: true }))

    renderList()

    expect(screen.getByTestId('engagement-list-skeleton')).toBeInTheDocument()
  })

  it('shows error message when query errors with an Error instance', () => {
    mockedUseEngagements.mockReturnValue(
      listResult({ isError: true, error: new Error('Network timeout') }),
    )

    renderList()

    expect(screen.getByRole('alert')).toHaveTextContent('Network timeout')
  })

  it('shows fallback error message when error is not an Error instance', () => {
    mockedUseEngagements.mockReturnValue(
      listResult({ isError: true, error: 'string error' as unknown as Error }),
    )

    renderList()

    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load engagements.')
  })
})
