import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { auditKeys, useEngagementAudit, useGlobalAudit } from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

const ENTRY = {
  id: '00000000-0000-0000-0000-0000000000aa',
  seq: 1,
  action: 'login' as const,
  actor_user_id: '00000000-0000-0000-0000-0000000000bb',
  engagement_id: null,
  target_type: null,
  target_id: null,
  self_approved: null,
  payload: {},
  created_at: '2026-06-05T00:00:00Z',
  prev_hash: '0'.repeat(64),
  entry_hash: 'a'.repeat(64),
}
const PAGE = { items: [ENTRY], next_cursor: null }

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('auditKeys', () => {
  it('namespaces engagement vs global and folds filters', () => {
    expect(auditKeys.engagement(ENGAGEMENT_ID, { action: 'login' })).toEqual([
      'audit',
      'engagement',
      ENGAGEMENT_ID,
      { action: 'login' },
    ])
    expect(auditKeys.global()).toEqual(['audit', 'global', {}])
  })
})

describe('useEngagementAudit', () => {
  it('builds the query string including the self_approved + action filters', async () => {
    resolveGet({ data: PAGE, response: { status: 200 } })
    const { result } = renderHook(
      () => useEngagementAudit(ENGAGEMENT_ID, { action: 'approval_granted', selfApproved: true }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.pages[0]).toEqual(PAGE)
    expect(mockGet).toHaveBeenCalledWith('/api/v1/audit', {
      params: {
        query: {
          engagement_id: ENGAGEMENT_ID,
          limit: 50,
          action: 'approval_granted',
          self_approved: true,
        },
      },
    })
  })

  it('sends self_approved=false (not omitted) when explicitly false', async () => {
    resolveGet({ data: PAGE, response: { status: 200 } })
    const { result } = renderHook(
      () => useEngagementAudit(ENGAGEMENT_ID, { selfApproved: false }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockGet).toHaveBeenCalledWith('/api/v1/audit', {
      params: { query: { engagement_id: ENGAGEMENT_ID, limit: 50, self_approved: false } },
    })
  })

  it('paginates via next_cursor', async () => {
    resolveGet({ data: { items: [ENTRY], next_cursor: 'CURSOR1' }, response: { status: 200 } })
    const { result } = renderHook(() => useEngagementAudit(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)

    resolveGet({ data: { items: [], next_cursor: null }, response: { status: 200 } })
    await act(async () => {
      await result.current.fetchNextPage()
    })
    expect(mockGet).toHaveBeenLastCalledWith('/api/v1/audit', {
      params: {
        query: { engagement_id: ENGAGEMENT_ID, limit: 50, cursor: 'CURSOR1' },
      },
    })
  })

  it('is disabled without an engagement id', () => {
    renderHook(() => useEngagementAudit(''), { wrapper: createWrapper() })
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('surfaces a 404 (non-member) as an error', async () => {
    resolveGet({ error: { detail: 'not found' }, response: { status: 404 } })
    const { result } = renderHook(() => useEngagementAudit(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useGlobalAudit', () => {
  it('hits the global endpoint and surfaces a 403 as an error', async () => {
    resolveGet({ error: { detail: 'forbidden' }, response: { status: 403 } })
    const { result } = renderHook(() => useGlobalAudit(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(mockGet).toHaveBeenCalledWith('/api/v1/audit/global', {
      params: { query: { limit: 50 } },
    })
  })
})
