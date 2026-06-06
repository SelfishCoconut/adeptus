import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type AutonomyGrant,
  type DelegableReason,
} from '@/shared/api'
import { approvalKeys } from '@/features/approvals/api'

// --- Query keys ---

export const autonomyKeys = {
  all: ['autonomy'] as const,
  engagement: (engagementId: string) => ['autonomy', engagementId] as const,
}

// --- Query ---

/**
 * The engagement's active standing-autonomy grants (§5.2). Drives the Autonomy panel and
 * the "Always allow" card affordance (so a category already delegated hides its button).
 * Engagement-scoped + membership-gated server-side (404 for non-members).
 */
export function useAutonomyGrants(
  engagementId: string,
  options?: { enabled?: boolean },
) {
  return useQuery<AutonomyGrant[]>({
    queryKey: autonomyKeys.engagement(engagementId),
    enabled: (options?.enabled ?? true) && Boolean(engagementId),
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/autonomy-grants',
        { params: { path: { engagement_id: engagementId } } },
      )
      if (error || !data) throw new Error('Failed to load autonomy grants')
      return data
    },
  })
}

// --- Mutations ---

/**
 * Thrown when a grant returns 409: a grant for this category is already active (another
 * member delegated it in the race window between the card rendering and the click). The
 * category is already delegated, so callers can treat this as "already covered".
 */
export class AutonomyConflictError extends Error {
  constructor() {
    super('Standing autonomy is already active for this category')
    this.name = 'AutonomyConflictError'
  }
}

/**
 * Grant standing autonomy for one reason category (any member, §5.2). The server then
 * auto-approves future commands whose reasons are *all* covered. Invalidates the grants
 * list and the approval queue (a fresh grant can clear pending cards on the next turn).
 * A 409 surfaces as {@link AutonomyConflictError} so callers can distinguish "already
 * delegated" from a real failure.
 */
export function useGrantAutonomy(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<AutonomyGrant, Error, { reason: DelegableReason }>({
    mutationFn: async ({ reason }) => {
      const { data, error, response } = await api.POST(
        '/api/v1/engagements/{engagement_id}/autonomy-grants',
        { params: { path: { engagement_id: engagementId } }, body: { reason } },
      )
      if (response?.status === 409) throw new AutonomyConflictError()
      if (error || !data) throw new Error('Failed to grant autonomy')
      return data
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: autonomyKeys.engagement(engagementId) })
      void queryClient.invalidateQueries({ queryKey: approvalKeys.engagement(engagementId) })
    },
  })
}

/** Revoke a standing-autonomy grant (any member, §5.2); the next gated command of that
 * category gates with a human card again. */
export function useRevokeAutonomy(engagementId: string) {
  const queryClient = useQueryClient()
  return useMutation<void, Error, { grantId: string }>({
    mutationFn: async ({ grantId }) => {
      const { error } = await api.DELETE(
        '/api/v1/engagements/{engagement_id}/autonomy-grants/{grant_id}',
        { params: { path: { engagement_id: engagementId, grant_id: grantId } } },
      )
      if (error) throw new Error('Failed to revoke autonomy')
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: autonomyKeys.engagement(engagementId) })
      // A revoke re-gates that category next turn, so refresh the approval queue too
      // (symmetric with useGrantAutonomy).
      void queryClient.invalidateQueries({ queryKey: approvalKeys.engagement(engagementId) })
    },
  })
}
