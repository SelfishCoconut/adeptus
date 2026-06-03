import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/shared/api'
import type { components } from '@/shared/api'

// ---------------------------------------------------------------------------
// Generated types — never hand-written
// ---------------------------------------------------------------------------

export type GraphSnapshot = components['schemas']['GraphSnapshot']
export type GraphHistory = components['schemas']['GraphHistory']
export type Node = components['schemas']['Node']
export type NodeCreate = components['schemas']['NodeCreate']
export type NodeUpdate = components['schemas']['NodeUpdate']
export type Edge = components['schemas']['Edge']
export type EdgeCreate = components['schemas']['EdgeCreate']
export type UndoStack = components['schemas']['UndoStack']
export type UndoStackEntry = components['schemas']['UndoStackEntry']
export type UndoResult = components['schemas']['UndoResult']
export type UndoOpType = components['schemas']['UndoOpType']

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const graphKeys = {
  graph: (engagementId: string) =>
    ['graph', engagementId] as const,
  history: (engagementId: string) =>
    ['graph', engagementId, 'history'] as const,
  undoStack: (engagementId: string) =>
    ['graph', engagementId, 'undo-stack'] as const,
} as const

// ---------------------------------------------------------------------------
// Error extraction — surfaces structured server errors (422, 409, etc.)
// so dialogs can display them to the user.
// ---------------------------------------------------------------------------

function extractServerMessage(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const e = error as Record<string, unknown>
  // FastAPI 422 detail is an array of validation errors
  if (Array.isArray(e.detail)) {
    const first = e.detail[0] as Record<string, unknown> | undefined
    if (first && typeof first.msg === 'string') return first.msg
  }
  // Structured error body: { error: { code, message } }
  if (typeof e.error === 'object' && e.error !== null) {
    const inner = e.error as Record<string, unknown>
    if (typeof inner.message === 'string') return inner.message
  }
  // Plain string detail
  if (typeof e.detail === 'string') return e.detail
  return undefined
}

function toError(error: unknown, fallback: string): Error {
  const msg = extractServerMessage(error)
  return new Error(msg ?? fallback)
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/engagements/{engagement_id}/graph
 * Returns the full live graph (non-deleted nodes + edges) served from the
 * in-memory single writer.
 */
export function useGraph(engagementId: string) {
  return useQuery<GraphSnapshot>({
    queryKey: graphKeys.graph(engagementId),
    enabled: !!engagementId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/graph',
        { params: { path: { engagement_id: engagementId } } },
      )
      if (error || !data) throw toError(error, 'Failed to load graph')
      return data
    },
  })
}

/**
 * GET /api/v1/engagements/{engagement_id}/graph/history
 * Returns soft-deleted nodes and the per-entity node history.
 */
export function useGraphHistory(engagementId: string) {
  return useQuery<GraphHistory>({
    queryKey: graphKeys.history(engagementId),
    enabled: !!engagementId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/graph/history',
        { params: { path: { engagement_id: engagementId } } },
      )
      if (error || !data) throw toError(error, 'Failed to load graph history')
      return data
    },
  })
}

// ---------------------------------------------------------------------------
// Node mutations
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/engagements/{engagement_id}/graph/nodes
 * Invalidates the graph snapshot on success.
 */
export function useCreateNode(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<Node, Error, NodeCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/graph/nodes',
        {
          params: { path: { engagement_id: engagementId } },
          body,
        },
      )
      if (error || !data) throw toError(error, 'Failed to create node')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
    },
  })
}

/**
 * PATCH /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}
 * Invalidates graph + history on success.
 */
export function useUpdateNode(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<Node, Error, { nodeId: string } & NodeUpdate>({
    mutationFn: async ({ nodeId, ...body }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}',
        {
          params: { path: { engagement_id: engagementId, node_id: nodeId } },
          body,
        },
      )
      if (error || !data) throw toError(error, 'Failed to update node')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
    },
  })
}

/**
 * DELETE /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}
 * Soft-deletes the node (and cascades to incident edges).
 * Invalidates graph + history on success.
 */
export function useDeleteNode(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (nodeId) => {
      const { error } = await api.DELETE(
        '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}',
        {
          params: { path: { engagement_id: engagementId, node_id: nodeId } },
        },
      )
      if (error) throw toError(error, 'Failed to delete node')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
    },
  })
}

/**
 * POST /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo
 * Reverts the node to its immediately-prior state from history.
 * Invalidates graph + history on success.
 */
export function useUndoNode(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<Node, Error, string>({
    mutationFn: async (nodeId) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo',
        {
          params: { path: { engagement_id: engagementId, node_id: nodeId } },
        },
      )
      if (error || !data) throw toError(error, 'Failed to undo node')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
    },
  })
}

// ---------------------------------------------------------------------------
// Edge mutations
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/engagements/{engagement_id}/graph/edges
 * Creates a directed edge between two existing non-deleted nodes.
 * Invalidates graph on success.
 */
export function useCreateEdge(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<Edge, Error, EdgeCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/graph/edges',
        {
          params: { path: { engagement_id: engagementId } },
          body,
        },
      )
      if (error || !data) throw toError(error, 'Failed to create edge')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
    },
  })
}

/**
 * DELETE /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}
 * Soft-deletes the edge.
 * Invalidates graph + history on success.
 */
export function useDeleteEdge(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (edgeId) => {
      const { error } = await api.DELETE(
        '/api/v1/engagements/{engagement_id}/graph/edges/{edge_id}',
        {
          params: { path: { engagement_id: engagementId, edge_id: edgeId } },
        },
      )
      if (error) throw toError(error, 'Failed to delete edge')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
    },
  })
}

/**
 * POST /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}/undo
 * Reverts the edge to its prior state from history.
 * Invalidates graph + history on success.
 */
export function useUndoEdge(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<Edge, Error, string>({
    mutationFn: async (edgeId) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/graph/edges/{edge_id}/undo',
        {
          params: { path: { engagement_id: engagementId, edge_id: edgeId } },
        },
      )
      if (error || !data) throw toError(error, 'Failed to undo edge')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
    },
  })
}

// ---------------------------------------------------------------------------
// Personal undo stack (Slice 09)
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/engagements/{engagement_id}/graph/undo-stack
 * Returns the calling user's personal undo stack for this engagement,
 * newest-first. Per-user and per-engagement scoped.
 */
export function useUndoStack(engagementId: string) {
  return useQuery<UndoStack>({
    queryKey: graphKeys.undoStack(engagementId),
    enabled: !!engagementId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/graph/undo-stack',
        { params: { path: { engagement_id: engagementId } } },
      )
      if (error || !data) throw toError(error, 'Failed to load undo stack')
      return data
    },
  })
}

/**
 * POST /api/v1/engagements/{engagement_id}/graph/undo-stack/pop
 * Undoes the caller's most recent still-valid personal write.
 *
 * Always resolves with an UndoResult (never throws on an empty stack): a
 * `undone === null` result means there was nothing to undo (not an error), and
 * `skipped_stale` carries any entries dropped because a teammate (or the user)
 * changed the target since. Invalidates graph + history + undo-stack on success.
 */
export function usePopUndoStack(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<UndoResult, Error, void>({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/graph/undo-stack/pop',
        { params: { path: { engagement_id: engagementId } } },
      )
      if (error || !data) throw toError(error, 'Failed to undo')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: graphKeys.graph(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.history(engagementId) })
      void queryClient.invalidateQueries({ queryKey: graphKeys.undoStack(engagementId) })
    },
  })
}
