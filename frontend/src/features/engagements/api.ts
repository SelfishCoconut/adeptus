import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type AddMemberRequest,
  type EngagementCreate,
  type EngagementDetail,
  type EngagementPauseRequest,
  type EngagementPauseState,
  type EngagementSummary,
  type EngagementUpdate,
  type MemberEntry,
} from '@/shared/api'
import { toolQueueKey } from '@/shared/api/queryKeys'

// --- Query key constants ---

export const engagementsKey = ['engagements'] as const

export const engagementKey = (id: string) => ['engagements', id] as const

export const membersKey = (engagementId: string) =>
  ['engagements', engagementId, 'members'] as const

// --- Queries ---

export function useEngagements() {
  return useQuery<EngagementSummary[]>({
    queryKey: engagementsKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/engagements')
      if (error || !data) throw new Error('Failed to load engagements')
      return data
    },
    staleTime: 30_000,
  })
}

export function useEngagement(id: string) {
  return useQuery<EngagementDetail>({
    queryKey: engagementKey(id),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/engagements/{engagement_id}', {
        params: { path: { engagement_id: id } },
      })
      if (error || !data) throw new Error('Failed to load engagement')
      return data
    },
    staleTime: 30_000,
  })
}

export function useMembers(engagementId: string) {
  return useQuery<MemberEntry[]>({
    queryKey: membersKey(engagementId),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/engagements/{engagement_id}/members', {
        params: { path: { engagement_id: engagementId } },
      })
      if (error || !data) throw new Error('Failed to load members')
      return data
    },
    staleTime: 30_000,
  })
}

// --- Mutations ---

export function useCreateEngagement() {
  const queryClient = useQueryClient()
  return useMutation<EngagementDetail, Error, EngagementCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST('/api/v1/engagements', { body })
      if (error || !data) throw new Error('Failed to create engagement')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: engagementsKey })
    },
  })
}

export function useUpdateEngagement(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<EngagementDetail, Error, EngagementUpdate>({
    mutationFn: async (body) => {
      const { data, error } = await api.PATCH('/api/v1/engagements/{engagement_id}', {
        params: { path: { engagement_id: engagementId } },
        body,
      })
      if (error || !data) throw new Error('Failed to update engagement')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: engagementKey(engagementId) })
    },
  })
}

export function useAddMember(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<MemberEntry, Error, AddMemberRequest>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST('/api/v1/engagements/{engagement_id}/members', {
        params: { path: { engagement_id: engagementId } },
        body,
      })
      if (error || !data) throw new Error('Failed to add member')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: membersKey(engagementId) })
    },
  })
}

/**
 * Toggle the engagement-wide tool pause.
 *
 * Invalidation strategy: on success we invalidate both the engagement detail
 * query (the `paused` field) and the tool-queue snapshot. The tool-queue key is
 * imported from `shared/api/queryKeys` — the single source of truth for cross-
 * feature query key constants — so both this module and `mcp/api.ts` stay in sync
 * automatically if the key shape ever changes.
 */
export function useEngagementPause(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<EngagementPauseState, Error, EngagementPauseRequest>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST('/api/v1/engagements/{engagement_id}/pause', {
        params: { path: { engagement_id: engagementId } },
        body,
      })
      if (error || !data) throw new Error('Failed to set engagement pause state')
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: engagementKey(engagementId) })
      void queryClient.invalidateQueries({ queryKey: toolQueueKey(engagementId) })
    },
  })
}

export function useRemoveMember(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (userId) => {
      const { error } = await api.DELETE(
        '/api/v1/engagements/{engagement_id}/members/{user_id}',
        {
          params: { path: { engagement_id: engagementId, user_id: userId } },
        },
      )
      if (error) throw new Error('Failed to remove member')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: membersKey(engagementId) })
    },
  })
}
