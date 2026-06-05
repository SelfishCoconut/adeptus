import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ApprovalRequest, type ApprovalRequestPage, type ApprovalStatus } from '@/shared/api'
import { chatKeys } from '@/features/chat/api'

// --- Query keys ---

export const approvalKeys = {
  all: ['approvals'] as const,
  engagement: (engagementId: string) => ['approvals', engagementId] as const,
  list: (engagementId: string, status?: ApprovalStatus) =>
    ['approvals', engagementId, status ?? 'all'] as const,
}

/**
 * Thrown when a decision endpoint returns 409 (§5.2 / Risk 1): the request was already
 * decided by another member (carries the terminal `status`) or the engagement is archived
 * (§4). The card surfaces this so a stale "Approve"/"Reject" click shows what happened.
 */
export class ApprovalConflictError extends Error {
  readonly reason: 'already_decided' | 'engagement_archived'
  readonly status?: ApprovalStatus
  constructor(reason: 'already_decided' | 'engagement_archived', status?: ApprovalStatus) {
    super(reason === 'engagement_archived' ? 'Engagement is archived' : 'Already decided')
    this.name = 'ApprovalConflictError'
    this.reason = reason
    this.status = status
  }
}

function conflictFrom(error: unknown, status: number | undefined): ApprovalConflictError | null {
  if (status !== 409 || !error || typeof error !== 'object') return null
  const body = error as Record<string, unknown>
  if (body.reason === 'already_decided' || body.reason === 'engagement_archived') {
    return new ApprovalConflictError(
      body.reason,
      typeof body.status === 'string' ? (body.status as ApprovalStatus) : undefined,
    )
  }
  return null
}

const PAGE_LIMIT = 50

// --- Query ---

/**
 * The engagement's shared approval queue (§5.2). `status: 'pending'` drives the Approvals
 * tab; a card polls/invalidates this so another member's decision shows up. Engagement-
 * scoped + membership-gated server-side (404 for non-members).
 */
export function useApprovalRequests(
  engagementId: string,
  options?: { status?: ApprovalStatus; enabled?: boolean; refetchInterval?: number },
) {
  return useQuery<ApprovalRequestPage>({
    queryKey: approvalKeys.list(engagementId, options?.status),
    enabled: (options?.enabled ?? true) && Boolean(engagementId),
    refetchInterval: options?.refetchInterval,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/engagements/{engagement_id}/approvals', {
        params: {
          path: { engagement_id: engagementId },
          query: { limit: PAGE_LIMIT, ...(options?.status ? { status: options.status } : {}) },
        },
      })
      if (error || !data) throw new Error('Failed to load approval requests')
      return data
    },
  })
}

// --- Mutations ---

function useDecision(engagementId: string, action: 'approve' | 'reject') {
  const queryClient = useQueryClient()
  return useMutation<ApprovalRequest, Error, { requestId: string }>({
    mutationFn: async ({ requestId }) => {
      const params = { path: { engagement_id: engagementId, request_id: requestId } }
      const { data, error, response } =
        action === 'approve'
          ? await api.POST(
              '/api/v1/engagements/{engagement_id}/approvals/{request_id}/approve',
              { params },
            )
          : await api.POST(
              '/api/v1/engagements/{engagement_id}/approvals/{request_id}/reject',
              { params },
            )
      if (error || !data) {
        const conflict = conflictFrom(error, response?.status)
        if (conflict) throw conflict
        throw new Error(`Failed to ${action} request`)
      }
      return data
    },
    // Refresh the shared queue AND the chat thread so the inline card + the tab both
    // reflect the new decision (§5.2 attribution shown inline).
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: approvalKeys.engagement(engagementId) })
      void queryClient.invalidateQueries({ queryKey: chatKeys.conversation(engagementId) })
    },
  })
}

/** Approve a pending dangerous command (any member, §5.2); the server then runs it. */
export function useApproveRequest(engagementId: string) {
  return useDecision(engagementId, 'approve')
}

/** Reject a pending dangerous command (symmetric, §5.2); never executed. */
export function useRejectRequest(engagementId: string) {
  return useDecision(engagementId, 'reject')
}
