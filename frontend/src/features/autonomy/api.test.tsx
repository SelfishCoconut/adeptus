import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { autonomyKeys, useAutonomyGrants, useGrantAutonomy, useRevokeAutonomy } from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockDelete = vi.mocked(api.DELETE)

type FetchResult = { data?: unknown; error?: unknown; response?: { status: number } }
const resolveGet = (v: FetchResult) => mockGet.mockResolvedValue(v as never)
const resolvePost = (v: FetchResult) => mockPost.mockResolvedValue(v as never)
const resolveDelete = (v: FetchResult) => mockDelete.mockResolvedValue(v as never)

const ENG = 'eng-1'

const grant = (overrides: Record<string, unknown> = {}) => ({
  id: 'grant-1',
  engagement_id: ENG,
  reason: 'aggressive_scan',
  granted_by_user_id: 'user-1',
  granted_by_username: 'pentester',
  created_at: '2026-06-06T00:00:00Z',
  revoked_at: null,
  ...overrides,
})

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
  mockDelete.mockReset()
})

describe('autonomyKeys', () => {
  it('namespaces grants by engagement', () => {
    expect(autonomyKeys.engagement(ENG)).toEqual(['autonomy', ENG])
  })
})

describe('useAutonomyGrants', () => {
  it('loads the engagement active grants', async () => {
    resolveGet({ data: [grant()] })
    const { result } = renderHook(() => useAutonomyGrants(ENG), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
    const call = mockGet.mock.calls[0][1] as { params: { path: Record<string, unknown> } }
    expect(call.params.path.engagement_id).toBe(ENG)
  })

  it('does not fetch without an engagement id', () => {
    renderHook(() => useAutonomyGrants(''), { wrapper: createWrapper() })
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('surfaces a load failure', async () => {
    resolveGet({ error: { detail: 'nope' }, response: { status: 404 } })
    const { result } = renderHook(() => useAutonomyGrants(ENG), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useGrantAutonomy', () => {
  it('grants a category and returns the new grant', async () => {
    resolvePost({ data: grant() })
    const { result } = renderHook(() => useGrantAutonomy(ENG), { wrapper: createWrapper() })
    const created = await result.current.mutateAsync({ reason: 'aggressive_scan' })
    expect(created.reason).toBe('aggressive_scan')
    const call = mockPost.mock.calls[0][1] as { body: { reason: string } }
    expect(call.body.reason).toBe('aggressive_scan')
  })

  it('throws on a rejected grant (409 already active)', async () => {
    resolvePost({ error: { detail: 'already active' }, response: { status: 409 } })
    const { result } = renderHook(() => useGrantAutonomy(ENG), { wrapper: createWrapper() })
    await expect(result.current.mutateAsync({ reason: 'out_of_scope' })).rejects.toThrow()
  })
})

describe('useRevokeAutonomy', () => {
  it('revokes via the delete endpoint', async () => {
    resolveDelete({ data: undefined })
    const { result } = renderHook(() => useRevokeAutonomy(ENG), { wrapper: createWrapper() })
    await result.current.mutateAsync({ grantId: 'grant-1' })
    const call = mockDelete.mock.calls[0][1] as { params: { path: Record<string, unknown> } }
    expect(call.params.path.grant_id).toBe('grant-1')
  })

  it('throws when revoke fails', async () => {
    resolveDelete({ error: { detail: 'gone' }, response: { status: 404 } })
    const { result } = renderHook(() => useRevokeAutonomy(ENG), { wrapper: createWrapper() })
    await expect(result.current.mutateAsync({ grantId: 'grant-1' })).rejects.toThrow()
  })
})
