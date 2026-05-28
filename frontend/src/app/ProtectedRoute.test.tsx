import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
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

function renderProtected() {
  return render(
    <MemoryRouter initialEntries={['/workspace']}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route
          path="/workspace"
          element={
            <ProtectedRoute>
              <div>protected content</div>
            </ProtectedRoute>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    mockedUseMe.mockReset()
  })

  it('renders children when a session exists', () => {
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
    renderProtected()

    expect(screen.getByText('protected content')).toBeInTheDocument()
  })

  it('redirects to /login when there is no session', () => {
    mockedUseMe.mockReturnValue(meResult({ data: null }))
    renderProtected()

    expect(screen.getByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders nothing while the session is loading', () => {
    mockedUseMe.mockReturnValue(meResult({ isPending: true }))
    renderProtected()

    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
    expect(screen.queryByText('login page')).not.toBeInTheDocument()
  })
})
