import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AdminRoute } from './AdminRoute'
import { useMe } from '@/features/auth/api'

vi.mock('@/features/auth/api', () => ({
  useMe: vi.fn(),
}))

const mockedUseMe = vi.mocked(useMe)

function meResult(overrides: { isPending?: boolean; data?: unknown }) {
  return {
    isPending: false,
    data: undefined,
    ...overrides,
  } as unknown as ReturnType<typeof useMe>
}

function renderAdminRoute() {
  return render(
    <MemoryRouter initialEntries={['/admin/mcp-servers']}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/engagements" element={<div>engagements page</div>} />
        <Route
          path="/admin/mcp-servers"
          element={
            <AdminRoute>
              <div>admin content</div>
            </AdminRoute>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AdminRoute', () => {
  beforeEach(() => {
    mockedUseMe.mockReset()
  })

  it('renders children for an admin user', () => {
    mockedUseMe.mockReturnValue(
      meResult({
        data: {
          id: '00000000-0000-0000-0000-000000000001',
          username: 'admin',
          role: 'admin',
          terms_accepted_at: '2026-01-01T00:00:00Z',
        },
      }),
    )
    renderAdminRoute()

    expect(screen.getByText('admin content')).toBeInTheDocument()
  })

  it('redirects to /engagements for an authenticated non-admin user', () => {
    mockedUseMe.mockReturnValue(
      meResult({
        data: {
          id: '00000000-0000-0000-0000-000000000002',
          username: 'alice',
          role: 'user',
          terms_accepted_at: '2026-01-01T00:00:00Z',
        },
      }),
    )
    renderAdminRoute()

    expect(screen.getByText('engagements page')).toBeInTheDocument()
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
  })

  it('redirects to /login when there is no session', () => {
    mockedUseMe.mockReturnValue(meResult({ data: null }))
    renderAdminRoute()

    expect(screen.getByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
  })

  it('renders nothing while the session is loading', () => {
    mockedUseMe.mockReturnValue(meResult({ isPending: true }))
    renderAdminRoute()

    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
    expect(screen.queryByText('login page')).not.toBeInTheDocument()
    expect(screen.queryByText('engagements page')).not.toBeInTheDocument()
  })
})
