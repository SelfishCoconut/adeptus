import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAcceptTerms, useLogin, useLogout, useMe } from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)
const resolvePost = (value: FetchResult) => mockPost.mockResolvedValue(value as never)

const user = {
  id: '00000000-0000-0000-0000-000000000001',
  username: 'admin',
  role: 'admin' as const,
  terms_accepted_at: null,
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
})

describe('useMe', () => {
  it('returns the user on 200', async () => {
    resolveGet({ data: user, response: { status: 200 } })
    const { result } = renderHook(() => useMe(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(user)
  })

  it('resolves to null on 401', async () => {
    resolveGet({ data: undefined, response: { status: 401 } })
    const { result } = renderHook(() => useMe(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
  })

  it('errors on a non-401 failure', async () => {
    resolveGet({ data: undefined, response: { status: 500 } })
    const { result } = renderHook(() => useMe(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useLogin', () => {
  it('returns the user on success', async () => {
    resolvePost({ data: user, response: { status: 200 } })
    const { result } = renderHook(() => useLogin(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({ username: 'admin', password: 'pw' })
    })
    expect(returned).toEqual(user)
  })

  it('throws a credential error on 401', async () => {
    resolvePost({ error: { detail: 'nope' }, response: { status: 401 } })
    const { result } = renderHook(() => useLogin(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ username: 'admin', password: 'bad' }),
      ).rejects.toThrow(/invalid username or password/i)
    })
  })
})

describe('useLogout', () => {
  it('resolves on a clean logout', async () => {
    resolvePost({ response: { status: 204 } })
    const { result } = renderHook(() => useLogout(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync()).resolves.toBeUndefined()
    })
  })

  it('treats a 401 as an already-completed logout', async () => {
    resolvePost({ error: { detail: 'no session' }, response: { status: 401 } })
    const { result } = renderHook(() => useLogout(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync()).resolves.toBeUndefined()
    })
  })

  it('throws on a server error', async () => {
    resolvePost({ error: { detail: 'boom' }, response: { status: 500 } })
    const { result } = renderHook(() => useLogout(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toThrow(/logout failed/i)
    })
  })
})

describe('useAcceptTerms', () => {
  it('returns the updated user on success', async () => {
    const accepted = { ...user, terms_accepted_at: '2026-01-01T00:00:00Z' }
    resolvePost({ data: accepted, response: { status: 200 } })
    const { result } = renderHook(() => useAcceptTerms(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync()
    })
    expect(returned).toEqual(accepted)
  })

  it('throws when the request fails', async () => {
    resolvePost({ error: { detail: 'boom' }, response: { status: 500 } })
    const { result } = renderHook(() => useAcceptTerms(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toThrow(/record terms acceptance/i)
    })
  })
})
