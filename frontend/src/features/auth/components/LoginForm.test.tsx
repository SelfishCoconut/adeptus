import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LoginForm } from './LoginForm'
import { useLogin } from '../api'

const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../api', () => ({
  useLogin: vi.fn(),
}))

const mockedUseLogin = vi.mocked(useLogin)

type LoginMock = {
  mutate?: unknown
  isPending?: boolean
  isError?: boolean
  error?: Error | null
}

function loginResult(overrides: LoginMock = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useLogin>
}

describe('LoginForm', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    mockedUseLogin.mockReset()
  })

  it('renders username, password, and submit', () => {
    mockedUseLogin.mockReturnValue(loginResult({}))
    render(<LoginForm />)

    expect(screen.getByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('submits credentials and navigates to the workspace on success', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn(
      (
        _vars: { username: string; password: string },
        opts?: { onSuccess?: (user: unknown) => void },
      ) => {
        opts?.onSuccess?.({
          id: '00000000-0000-0000-0000-000000000001',
          username: 'admin',
          role: 'admin',
          terms_accepted_at: '2026-01-01T00:00:00Z',
        })
      },
    )
    mockedUseLogin.mockReturnValue(loginResult({ mutate }))
    render(<LoginForm />)

    await user.type(screen.getByLabelText(/username/i), 'admin')
    await user.type(screen.getByLabelText(/password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(mutate).toHaveBeenCalledWith(
      { username: 'admin', password: 'secret' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    expect(mockNavigate).toHaveBeenCalledWith('/workspace')
  })

  it('shows an error message when login fails', () => {
    mockedUseLogin.mockReturnValue(
      loginResult({ isError: true, error: new Error('Invalid username or password') }),
    )
    render(<LoginForm />)

    expect(screen.getByRole('alert')).toHaveTextContent(/invalid username or password/i)
    expect(mockNavigate).not.toHaveBeenCalled()
  })
})
