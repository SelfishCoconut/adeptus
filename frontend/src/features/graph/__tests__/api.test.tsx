import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import {
  graphKeys,
  useCreateEdge,
  useCreateNode,
  useDeleteEdge,
  useDeleteNode,
  useGraph,
  useGraphHistory,
  useUndoEdge,
  useUndoNode,
  useUpdateNode,
} from '../api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockDelete = vi.mocked(api.DELETE)
const mockPatch = vi.mocked(api.PATCH)

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)
const resolvePost = (value: FetchResult) => mockPost.mockResolvedValue(value as never)
const resolveDelete = (value: FetchResult) => mockDelete.mockResolvedValue(value as never)
const resolvePatch = (value: FetchResult) => mockPatch.mockResolvedValue(value as never)

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const NODE_ID = '00000000-0000-0000-0000-000000000002'
const EDGE_ID = '00000000-0000-0000-0000-000000000003'
const NODE_ID_2 = '00000000-0000-0000-0000-000000000004'

const NODE = {
  id: NODE_ID,
  engagement_id: ENGAGEMENT_ID,
  type: 'host' as const,
  label: '10.0.0.5',
  properties: { os: 'linux' },
  deleted: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const EDGE = {
  id: EDGE_ID,
  engagement_id: ENGAGEMENT_ID,
  source_id: NODE_ID,
  target_id: NODE_ID_2,
  relation: 'runs',
  properties: {},
  deleted: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const GRAPH_SNAPSHOT = {
  nodes: [NODE],
  edges: [EDGE],
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
  mockPatch.mockReset()
})

// ---------------------------------------------------------------------------
// Query key helpers
// ---------------------------------------------------------------------------

describe('graphKeys', () => {
  it('graph key is namespaced by engagementId', () => {
    expect(graphKeys.graph(ENGAGEMENT_ID)).toEqual(['graph', ENGAGEMENT_ID])
  })

  it('history key is namespaced by engagementId', () => {
    expect(graphKeys.history(ENGAGEMENT_ID)).toEqual(['graph', ENGAGEMENT_ID, 'history'])
  })
})

// ---------------------------------------------------------------------------
// useGraph
// ---------------------------------------------------------------------------

describe('useGraph', () => {
  it('returns the graph snapshot on a 200 response', async () => {
    resolveGet({ data: GRAPH_SNAPSHOT, response: { status: 200 } })
    const { result } = renderHook(() => useGraph(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(GRAPH_SNAPSHOT)
    expect(mockGet).toHaveBeenCalledWith('/api/v1/engagements/{engagement_id}/graph', {
      params: { path: { engagement_id: ENGAGEMENT_ID } },
    })
  })

  it('throws on error response', async () => {
    resolveGet({ data: undefined, error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useGraph(ENGAGEMENT_ID), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })

  it('is disabled when engagementId is empty', () => {
    const { result } = renderHook(() => useGraph(''), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGet).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// useGraphHistory
// ---------------------------------------------------------------------------

describe('useGraphHistory', () => {
  it('returns the graph history on a 200 response', async () => {
    const history = {
      deleted_nodes: [{ ...NODE, deleted: true }],
      node_history: [],
    }
    resolveGet({ data: history, response: { status: 200 } })
    const { result } = renderHook(() => useGraphHistory(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(history)
    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/history',
      { params: { path: { engagement_id: ENGAGEMENT_ID } } },
    )
  })

  it('throws on error response', async () => {
    resolveGet({ data: undefined, error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useGraphHistory(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })

  it('is disabled when engagementId is empty', () => {
    const { result } = renderHook(() => useGraphHistory(''), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGet).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// useCreateNode
// ---------------------------------------------------------------------------

describe('useCreateNode', () => {
  it('returns the created node on success', async () => {
    resolvePost({ data: NODE, response: { status: 201 } })
    const { result } = renderHook(() => useCreateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({
        type: 'host',
        label: '10.0.0.5',
        properties: { os: 'linux' },
      })
    })

    expect(returned).toEqual(NODE)
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/nodes',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID } },
        body: { type: 'host', label: '10.0.0.5', properties: { os: 'linux' } },
      },
    )
  })

  it('throws on 422 (validation error) with server message', async () => {
    resolvePost({
      error: { detail: [{ loc: ['body', 'type'], msg: 'invalid node type', type: 'enum' }] },
      response: { status: 422 },
    })
    const { result } = renderHook(() => useCreateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ type: 'host', label: 'x' }),
      ).rejects.toThrow('invalid node type')
    })
  })

  it('throws on 409 (engagement archived)', async () => {
    resolvePost({
      error: { detail: 'Engagement is archived' },
      response: { status: 409 },
    })
    const { result } = renderHook(() => useCreateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ type: 'host', label: 'x' }),
      ).rejects.toThrow('Engagement is archived')
    })
  })

  it('throws on 404 (non-member)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useCreateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ type: 'host', label: 'x' }),
      ).rejects.toThrow()
    })
  })

  it('invalidates graph key on success', async () => {
    resolvePost({ data: NODE, response: { status: 201 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useCreateNode(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ type: 'host', label: '10.0.0.5' })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useUpdateNode
// ---------------------------------------------------------------------------

describe('useUpdateNode', () => {
  const updatedNode = { ...NODE, label: '10.0.0.99' }

  it('returns the updated node on success', async () => {
    resolvePatch({ data: updatedNode, response: { status: 200 } })
    const { result } = renderHook(() => useUpdateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({ nodeId: NODE_ID, label: '10.0.0.99' })
    })

    expect(returned).toEqual(updatedNode)
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, node_id: NODE_ID } },
        body: { label: '10.0.0.99' },
      },
    )
  })

  it('throws on 409 (engagement archived)', async () => {
    resolvePatch({ error: { detail: 'Engagement is archived' }, response: { status: 409 } })
    const { result } = renderHook(() => useUpdateNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ nodeId: NODE_ID, label: 'x' }),
      ).rejects.toThrow('Engagement is archived')
    })
  })

  it('invalidates graph and history keys on success', async () => {
    resolvePatch({ data: updatedNode, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useUpdateNode(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ nodeId: NODE_ID, label: '10.0.0.99' })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.history(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useDeleteNode
// ---------------------------------------------------------------------------

describe('useDeleteNode', () => {
  it('resolves void on a successful soft-delete (204)', async () => {
    resolveDelete({ response: { status: 204 } })
    const { result } = renderHook(() => useDeleteNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(NODE_ID)).resolves.toBeUndefined()
    })

    expect(mockDelete).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, node_id: NODE_ID } },
      },
    )
  })

  it('throws on 409 (engagement archived)', async () => {
    resolveDelete({ error: { detail: 'Engagement is archived' }, response: { status: 409 } })
    const { result } = renderHook(() => useDeleteNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(NODE_ID)).rejects.toThrow('Engagement is archived')
    })
  })

  it('invalidates graph and history keys on success', async () => {
    resolveDelete({ response: { status: 204 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useDeleteNode(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(NODE_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.history(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useUndoNode
// ---------------------------------------------------------------------------

describe('useUndoNode', () => {
  it('returns the restored node on success', async () => {
    const restoredNode = { ...NODE, deleted: false }
    resolvePost({ data: restoredNode, response: { status: 200 } })
    const { result } = renderHook(() => useUndoNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync(NODE_ID)
    })

    expect(returned).toEqual(restoredNode)
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, node_id: NODE_ID } },
      },
    )
  })

  it('throws on 404 (no prior state to revert to)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useUndoNode(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(NODE_ID)).rejects.toThrow()
    })
  })

  it('invalidates graph and history keys on success', async () => {
    resolvePost({ data: NODE, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useUndoNode(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(NODE_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.history(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useCreateEdge
// ---------------------------------------------------------------------------

describe('useCreateEdge', () => {
  it('returns the created edge on success', async () => {
    resolvePost({ data: EDGE, response: { status: 201 } })
    const { result } = renderHook(() => useCreateEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({
        source_id: NODE_ID,
        target_id: NODE_ID_2,
        relation: 'runs',
      })
    })

    expect(returned).toEqual(EDGE)
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/edges',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID } },
        body: { source_id: NODE_ID, target_id: NODE_ID_2, relation: 'runs' },
      },
    )
  })

  it('throws on 409 (duplicate live edge)', async () => {
    resolvePost({ error: { detail: 'Duplicate edge' }, response: { status: 409 } })
    const { result } = renderHook(() => useCreateEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ source_id: NODE_ID, target_id: NODE_ID_2, relation: 'runs' }),
      ).rejects.toThrow('Duplicate edge')
    })
  })

  it('throws on 404 (source or target node not found)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useCreateEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ source_id: NODE_ID, target_id: NODE_ID_2, relation: 'runs' }),
      ).rejects.toThrow()
    })
  })

  it('invalidates graph key on success', async () => {
    resolvePost({ data: EDGE, response: { status: 201 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useCreateEdge(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ source_id: NODE_ID, target_id: NODE_ID_2, relation: 'runs' })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useDeleteEdge
// ---------------------------------------------------------------------------

describe('useDeleteEdge', () => {
  it('resolves void on a successful soft-delete (204)', async () => {
    resolveDelete({ response: { status: 204 } })
    const { result } = renderHook(() => useDeleteEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(EDGE_ID)).resolves.toBeUndefined()
    })

    expect(mockDelete).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/edges/{edge_id}',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, edge_id: EDGE_ID } },
      },
    )
  })

  it('throws on 409 (engagement archived)', async () => {
    resolveDelete({ error: { detail: 'Engagement is archived' }, response: { status: 409 } })
    const { result } = renderHook(() => useDeleteEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(EDGE_ID)).rejects.toThrow('Engagement is archived')
    })
  })

  it('invalidates graph and history keys on success', async () => {
    resolveDelete({ response: { status: 204 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useDeleteEdge(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(EDGE_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.history(ENGAGEMENT_ID) })
  })
})

// ---------------------------------------------------------------------------
// useUndoEdge
// ---------------------------------------------------------------------------

describe('useUndoEdge', () => {
  it('returns the restored edge on success', async () => {
    const restoredEdge = { ...EDGE, deleted: false }
    resolvePost({ data: restoredEdge, response: { status: 200 } })
    const { result } = renderHook(() => useUndoEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync(EDGE_ID)
    })

    expect(returned).toEqual(restoredEdge)
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/engagements/{engagement_id}/graph/edges/{edge_id}/undo',
      {
        params: { path: { engagement_id: ENGAGEMENT_ID, edge_id: EDGE_ID } },
      },
    )
  })

  it('throws on 404 (no prior state to revert to)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useUndoEdge(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(EDGE_ID)).rejects.toThrow()
    })
  })

  it('invalidates graph and history keys on success', async () => {
    resolvePost({ data: EDGE, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useUndoEdge(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(EDGE_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.graph(ENGAGEMENT_ID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: graphKeys.history(ENGAGEMENT_ID) })
  })
})
