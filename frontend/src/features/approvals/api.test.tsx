import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import {
  ApprovalConflictError,
  approvalKeys,
  useApprovalRequests,
  useApproveRequest,
  useRejectRequest,
} from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

type FetchResult = { data?: unknown; error?: unknown; response?: { status: number } }
const resolveGet = (v: FetchResult) => mockGet.mockResolvedValue(v as never)
const resolvePost = (v: FetchResult) => mockPost.mockResolvedValue(v as never)

const ENG = 'eng-1'

const request = (overrides: Record<string, unknown> = {}) => ({
  id: 'req-1',
  engagement_id: ENG,
  chat_message_id: 'msg-1',
  initiator_user_id: 'user-1',
  server_name: 'shell-exec',
  tool_name: 'run',
  args: { cmd: 'hydra' },
  reasons: ['credential_attack'],
  status: 'pending',
  created_at: '2026-06-05T00:00:00Z',
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
})

describe('approvalKeys', () => {
  it('namespaces list by engagement + status', () => {
    expect(approvalKeys.list(ENG, 'pending')).toEqual(['approvals', ENG, 'pending'])
    expect(approvalKeys.list(ENG)).toEqual(['approvals', ENG, 'all'])
  })
})

describe('useApprovalRequests', () => {
  it('passes the pending status filter in the query string', async () => {
    resolveGet({ data: { items: [request()], next_cursor: null } })
    const { result } = renderHook(() => useApprovalRequests(ENG, { status: 'pending' }), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.items).toHaveLength(1)
    const call = mockGet.mock.calls[0][1] as { params: { query: Record<string, unknown> } }
    expect(call.params.query.status).toBe('pending')
  })

  it('surfaces a load failure', async () => {
    resolveGet({ error: { detail: 'nope' }, response: { status: 404 } })
    const { result } = renderHook(() => useApprovalRequests(ENG), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useApproveRequest', () => {
  it('approves and returns the decided request', async () => {
    resolvePost({ data: request({ status: 'approved', self_approved: true, acted_by_username: 'me' }) })
    const { result } = renderHook(() => useApproveRequest(ENG), { wrapper: createWrapper() })
    const decided = await result.current.mutateAsync({ requestId: 'req-1' })
    expect(decided.status).toBe('approved')
    expect(mockPost.mock.calls[0][0]).toContain('/approve')
  })

  it('throws ApprovalConflictError with the terminal status on 409 already_decided', async () => {
    resolvePost({
      error: { reason: 'already_decided', status: 'rejected' },
      response: { status: 409 },
    })
    const { result } = renderHook(() => useApproveRequest(ENG), { wrapper: createWrapper() })
    await expect(result.current.mutateAsync({ requestId: 'req-1' })).rejects.toMatchObject({
      name: 'ApprovalConflictError',
      reason: 'already_decided',
      status: 'rejected',
    })
  })

  it('throws ApprovalConflictError for an archived engagement', async () => {
    resolvePost({ error: { reason: 'engagement_archived' }, response: { status: 409 } })
    const { result } = renderHook(() => useApproveRequest(ENG), { wrapper: createWrapper() })
    await expect(result.current.mutateAsync({ requestId: 'req-1' })).rejects.toBeInstanceOf(
      ApprovalConflictError,
    )
  })
})

describe('useRejectRequest', () => {
  it('rejects via the reject endpoint', async () => {
    resolvePost({ data: request({ status: 'rejected', self_approved: false, acted_by_username: 'me' }) })
    const { result } = renderHook(() => useRejectRequest(ENG), { wrapper: createWrapper() })
    const decided = await result.current.mutateAsync({ requestId: 'req-1' })
    expect(decided.status).toBe('rejected')
    expect(mockPost.mock.calls[0][0]).toContain('/reject')
  })
})
