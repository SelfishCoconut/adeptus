import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import {
  engagementKey,
  engagementsKey,
  membersKey,
  useAddMember,
  useCreateEngagement,
  useEngagement,
  useEngagements,
  useMembers,
  useRemoveMember,
} from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockDelete = vi.mocked(api.DELETE)

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)
const resolvePost = (value: FetchResult) => mockPost.mockResolvedValue(value as never)
const resolveDelete = (value: FetchResult) => mockDelete.mockResolvedValue(value as never)

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const USER_ID = '00000000-0000-0000-0000-000000000002'

const ENGAGEMENT_SUMMARY = {
  id: ENGAGEMENT_ID,
  name: 'Alpha Pentest',
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
  member_role: 'owner' as const,
}

const ENGAGEMENT_DETAIL = {
  id: ENGAGEMENT_ID,
  name: 'Alpha Pentest',
  status: 'active' as const,
  scope: '192.168.1.0/24',
  client_info: 'ACME Corp',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  member_role: 'owner' as const,
}

const MEMBER_ENTRY = {
  user_id: USER_ID,
  username: 'bob',
  role: 'member' as const,
  joined_at: '2026-01-02T00:00:00Z',
}

// ---------------------------------------------------------------------------
// Wrapper helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Query key helpers
// ---------------------------------------------------------------------------

describe('query key helpers', () => {
  it('engagementsKey is a stable tuple', () => {
    expect(engagementsKey).toEqual(['engagements'])
  })

  it('engagementKey includes the id', () => {
    expect(engagementKey(ENGAGEMENT_ID)).toEqual(['engagements', ENGAGEMENT_ID])
  })

  it('membersKey includes engagementId and members segment', () => {
    expect(membersKey(ENGAGEMENT_ID)).toEqual(['engagements', ENGAGEMENT_ID, 'members'])
  })
})

// ---------------------------------------------------------------------------
// useEngagements
// ---------------------------------------------------------------------------

describe('useEngagements', () => {
  it('returns the list on a 200 response', async () => {
    resolveGet({ data: [ENGAGEMENT_SUMMARY], response: { status: 200 } })
    const { result } = renderHook(() => useEngagements(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([ENGAGEMENT_SUMMARY])
    expect(mockGet).toHaveBeenCalledWith('/api/v1/engagements')
  })

  it('returns an empty array when the user has no engagements', async () => {
    resolveGet({ data: [], response: { status: 200 } })
    const { result } = renderHook(() => useEngagements(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it('throws when data is missing (error path)', async () => {
    resolveGet({ data: undefined, error: { detail: 'Unauthorized' }, response: { status: 401 } })
    const { result } = renderHook(() => useEngagements(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })

  it('throws when error is set even if no data', async () => {
    resolveGet({ error: { detail: 'server error' }, response: { status: 500 } })
    const { result } = renderHook(() => useEngagements(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })
})

// ---------------------------------------------------------------------------
// useEngagement
// ---------------------------------------------------------------------------

describe('useEngagement', () => {
  it('returns the engagement detail on a 200 response', async () => {
    resolveGet({ data: ENGAGEMENT_DETAIL, response: { status: 200 } })
    const { result } = renderHook(() => useEngagement(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(ENGAGEMENT_DETAIL)
    expect(mockGet).toHaveBeenCalledWith('/api/v1/engagements/{engagement_id}', {
      params: { path: { engagement_id: ENGAGEMENT_ID } },
    })
  })

  it('throws on 404 (non-member / not found)', async () => {
    resolveGet({ data: undefined, error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useEngagement(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })

  it('throws on 401 (unauthenticated)', async () => {
    resolveGet({ data: undefined, error: { detail: 'Unauthorized' }, response: { status: 401 } })
    const { result } = renderHook(() => useEngagement(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

// ---------------------------------------------------------------------------
// useMembers
// ---------------------------------------------------------------------------

describe('useMembers', () => {
  it('returns the members list on 200', async () => {
    resolveGet({ data: [MEMBER_ENTRY], response: { status: 200 } })
    const { result } = renderHook(() => useMembers(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([MEMBER_ENTRY])
    expect(mockGet).toHaveBeenCalledWith('/api/v1/engagements/{engagement_id}/members', {
      params: { path: { engagement_id: ENGAGEMENT_ID } },
    })
  })

  it('returns an empty array when the engagement has no members', async () => {
    resolveGet({ data: [], response: { status: 200 } })
    const { result } = renderHook(() => useMembers(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it('throws on error response (non-member gets 404)', async () => {
    resolveGet({ data: undefined, error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useMembers(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

// ---------------------------------------------------------------------------
// useCreateEngagement
// ---------------------------------------------------------------------------

describe('useCreateEngagement', () => {
  it('returns the created engagement on success', async () => {
    resolvePost({ data: ENGAGEMENT_DETAIL, response: { status: 201 } })
    const { result } = renderHook(() => useCreateEngagement(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({
        name: 'Alpha Pentest',
        scope: '192.168.1.0/24',
        client_info: 'ACME Corp',
      })
    })

    expect(returned).toEqual(ENGAGEMENT_DETAIL)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/engagements', {
      body: { name: 'Alpha Pentest', scope: '192.168.1.0/24', client_info: 'ACME Corp' },
    })
  })

  it('creates without client_info (optional field as null)', async () => {
    resolvePost({
      data: { ...ENGAGEMENT_DETAIL, client_info: null },
      response: { status: 201 },
    })
    const { result } = renderHook(() => useCreateEngagement(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({
        name: 'Alpha Pentest',
        scope: '192.168.1.0/24',
        client_info: null,
      })
    })
    expect(returned).toMatchObject({ client_info: null })
  })

  it('throws on a validation error (422)', async () => {
    resolvePost({
      error: { detail: [{ loc: ['body', 'name'], msg: 'too short', type: 'string_too_short' }] },
      response: { status: 422 },
    })
    const { result } = renderHook(() => useCreateEngagement(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ name: '', scope: '10.0.0.0/8', client_info: null }),
      ).rejects.toThrow('Failed to create engagement')
    })
  })

  it('invalidates the engagements list query on success', async () => {
    resolvePost({ data: ENGAGEMENT_DETAIL, response: { status: 201 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useCreateEngagement(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        name: 'Alpha Pentest',
        scope: '192.168.1.0/24',
        client_info: null,
      })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: engagementsKey })
  })
})

// ---------------------------------------------------------------------------
// useAddMember
// ---------------------------------------------------------------------------

describe('useAddMember', () => {
  it('returns the new member on success', async () => {
    resolvePost({ data: MEMBER_ENTRY, response: { status: 201 } })
    const { result } = renderHook(() => useAddMember(ENGAGEMENT_ID), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({ username: 'bob' })
    })

    expect(returned).toEqual(MEMBER_ENTRY)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/engagements/{engagement_id}/members', {
      params: { path: { engagement_id: ENGAGEMENT_ID } },
      body: { username: 'bob' },
    })
  })

  it('throws on conflict (user already a member)', async () => {
    resolvePost({
      error: { detail: 'User is already a member' },
      response: { status: 409 },
    })
    const { result } = renderHook(() => useAddMember(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync({ username: 'bob' })).rejects.toThrow(
        'Failed to add member',
      )
    })
  })

  it('throws on 403 (caller is not the owner)', async () => {
    resolvePost({
      error: { detail: 'Forbidden' },
      response: { status: 403 },
    })
    const { result } = renderHook(() => useAddMember(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync({ username: 'charlie' })).rejects.toThrow(
        'Failed to add member',
      )
    })
  })

  it('throws on 404 (unknown username)', async () => {
    resolvePost({
      error: { detail: 'Not Found' },
      response: { status: 404 },
    })
    const { result } = renderHook(() => useAddMember(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync({ username: 'nobody' })).rejects.toThrow(
        'Failed to add member',
      )
    })
  })

  it('invalidates members query on success', async () => {
    resolvePost({ data: MEMBER_ENTRY, response: { status: 201 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useAddMember(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ username: 'bob' })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: membersKey(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useRemoveMember
// ---------------------------------------------------------------------------

describe('useRemoveMember', () => {
  it('resolves void on a successful removal (204)', async () => {
    resolveDelete({ response: { status: 204 } })
    const { result } = renderHook(() => useRemoveMember(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(USER_ID)).resolves.toBeUndefined()
    })

    expect(mockDelete).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/members/{user_id}',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, user_id: USER_ID } },
      },
    )
  })

  it('throws on 403 (caller is not the owner)', async () => {
    resolveDelete({ error: { detail: 'Forbidden' }, response: { status: 403 } })
    const { result } = renderHook(() => useRemoveMember(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(USER_ID)).rejects.toThrow('Failed to remove member')
    })
  })

  it('throws on 400 (owner cannot remove themselves)', async () => {
    resolveDelete({ error: { detail: 'Owner cannot remove themselves' }, response: { status: 400 } })
    const { result } = renderHook(() => useRemoveMember(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(ENGAGEMENT_ID)).rejects.toThrow(
        'Failed to remove member',
      )
    })
  })

  it('throws on 404 (member not found)', async () => {
    resolveDelete({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useRemoveMember(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(USER_ID)).rejects.toThrow('Failed to remove member')
    })
  })

  it('invalidates members query on success', async () => {
    resolveDelete({ response: { status: 204 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useRemoveMember(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(USER_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: membersKey(ENGAGEMENT_ID) })
  })
})
