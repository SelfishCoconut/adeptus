import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import {
  findingsKeys,
  useCreateFinding,
  useDeleteFinding,
  useFinding,
  useFindings,
  useSetRemediation,
  useSetVerification,
  useUpdateFinding,
} from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockDelete = vi.mocked(api.DELETE)
const mockPatch = vi.mocked(api.PATCH)

type FetchResult = { data?: unknown; error?: unknown }
const resolveGet = (v: FetchResult) => mockGet.mockResolvedValue(v as never)
const resolvePost = (v: FetchResult) => mockPost.mockResolvedValue(v as never)
const resolveDelete = (v: FetchResult) => mockDelete.mockResolvedValue(v as never)
const resolvePatch = (v: FetchResult) => mockPatch.mockResolvedValue(v as never)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const FINDING_ID = '00000000-0000-0000-0000-000000000002'

const FINDING = {
  id: FINDING_ID,
  engagement_id: ENGAGEMENT_ID,
  title: 'Reflected XSS',
  description: '',
  severity: 'high' as const,
  verification_status: 'unverified' as const,
  remediation_status: 'open' as const,
  node_id: null,
  deleted: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
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
  mockDelete.mockReset()
  mockPatch.mockReset()
})

describe('findingsKeys', () => {
  it('list key is namespaced by engagement and include-deleted flag', () => {
    expect(findingsKeys.list(ENGAGEMENT_ID)).toEqual([
      'findings',
      ENGAGEMENT_ID,
      { includeDeleted: false },
    ])
    expect(findingsKeys.list(ENGAGEMENT_ID, true)).toEqual([
      'findings',
      ENGAGEMENT_ID,
      { includeDeleted: true },
    ])
  })

  it('detail key is namespaced by engagement and finding id', () => {
    expect(findingsKeys.detail(ENGAGEMENT_ID, FINDING_ID)).toEqual([
      'findings',
      ENGAGEMENT_ID,
      'detail',
      FINDING_ID,
    ])
  })
})

describe('useFindings', () => {
  it('returns the list on a 200 and passes include_deleted', async () => {
    resolveGet({ data: { items: [FINDING] } })
    const { result } = renderHook(() => useFindings(ENGAGEMENT_ID, true), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.items).toHaveLength(1)
    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/findings',
      {
        params: {
          path: { engagement_id: ENGAGEMENT_ID },
          query: { include_deleted: true },
        },
      },
    )
  })

  it('throws a structured server message on error', async () => {
    resolveGet({ error: { error: { code: 'not_found', message: 'Engagement not found' } } })
    const { result } = renderHook(() => useFindings(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toBe('Engagement not found')
  })
})

describe('useFinding', () => {
  it('returns one finding on a 200', async () => {
    resolveGet({ data: FINDING })
    const { result } = renderHook(() => useFinding(ENGAGEMENT_ID, FINDING_ID), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe(FINDING_ID)
  })
})

describe('useCreateFinding', () => {
  it('POSTs the body and resolves with the created finding', async () => {
    resolvePost({ data: FINDING })
    const { result } = renderHook(() => useCreateFinding(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    const created = await result.current.mutateAsync({
      title: 'XSS',
      severity: 'high',
      description: '',
    })
    expect(created.id).toBe(FINDING_ID)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/engagements/{engagement_id}/findings', {
      params: { path: { engagement_id: ENGAGEMENT_ID } },
      body: { title: 'XSS', severity: 'high', description: '' },
    })
  })

  it('surfaces a 422 detail message', async () => {
    resolvePost({ error: { detail: [{ msg: 'title must not be empty' }] } })
    const { result } = renderHook(() => useCreateFinding(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await expect(
      result.current.mutateAsync({ title: '', severity: 'low', description: '' }),
    ).rejects.toThrow('title must not be empty')
  })
})

describe('useUpdateFinding', () => {
  it('PATCHes the finding with the body (sans findingId)', async () => {
    resolvePatch({ data: FINDING })
    const { result } = renderHook(() => useUpdateFinding(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await result.current.mutateAsync({ findingId: FINDING_ID, title: 'New', node_id: null })
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/findings/{finding_id}',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, finding_id: FINDING_ID } },
        body: { title: 'New', node_id: null },
      },
    )
  })
})

describe('useSetVerification', () => {
  it('PATCHes the verification endpoint', async () => {
    resolvePatch({ data: FINDING })
    const { result } = renderHook(() => useSetVerification(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await result.current.mutateAsync({ findingId: FINDING_ID, verification_status: 'verified' })
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/findings/{finding_id}/verification',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, finding_id: FINDING_ID } },
        body: { verification_status: 'verified' },
      },
    )
  })
})

describe('useSetRemediation', () => {
  it('PATCHes the remediation endpoint', async () => {
    resolvePatch({ data: FINDING })
    const { result } = renderHook(() => useSetRemediation(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await result.current.mutateAsync({ findingId: FINDING_ID, remediation_status: 'fixed' })
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/findings/{finding_id}/remediation',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, finding_id: FINDING_ID } },
        body: { remediation_status: 'fixed' },
      },
    )
  })
})

describe('useDeleteFinding', () => {
  it('DELETEs the finding and resolves on 204', async () => {
    resolveDelete({})
    const { result } = renderHook(() => useDeleteFinding(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await result.current.mutateAsync(FINDING_ID)
    expect(mockDelete).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/findings/{finding_id}',
      { params: { path: { engagement_id: ENGAGEMENT_ID, finding_id: FINDING_ID } } },
    )
  })

  it('throws a fallback message when the error is unstructured', async () => {
    resolveDelete({ error: {} })
    const { result } = renderHook(() => useDeleteFinding(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })
    await expect(result.current.mutateAsync(FINDING_ID)).rejects.toThrow(
      'Failed to delete finding',
    )
  })
})
