import { useInfiniteQuery } from '@tanstack/react-query'
import { api, type AuditAction, type AuditPage } from '@/shared/api'

// --- Filters ---

export interface AuditFilters {
  action?: AuditAction
  /** §5.2 — filter approval entries by self_approved (cross-member vs self-approvals). */
  selfApproved?: boolean
}

export interface GlobalAuditFilters {
  action?: AuditAction
}

// --- Query key factory ---

export const auditKeys = {
  all: ['audit'] as const,
  engagement: (engagementId: string, filters?: AuditFilters) =>
    ['audit', 'engagement', engagementId, filters ?? {}] as const,
  global: (filters?: GlobalAuditFilters) => ['audit', 'global', filters ?? {}] as const,
}

const DEFAULT_PAGE_LIMIT = 50

/** Paginated (keyset, newest-first) audit entries for an engagement. Requires membership. */
export function useEngagementAudit(
  engagementId: string,
  filters?: AuditFilters,
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery<AuditPage>({
    queryKey: auditKeys.engagement(engagementId, filters),
    enabled: (options?.enabled ?? true) && Boolean(engagementId),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/audit', {
        params: {
          query: {
            engagement_id: engagementId,
            limit: DEFAULT_PAGE_LIMIT,
            ...(filters?.action ? { action: filters.action } : {}),
            ...(filters?.selfApproved !== undefined
              ? { self_approved: filters.selfApproved }
              : {}),
            ...(pageParam ? { cursor: pageParam as string } : {}),
          },
        },
      })
      if (error || !data) throw new Error('Failed to load audit log')
      return data
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })
}

/** Paginated instance-global (no-engagement) audit entries. Admin only. */
export function useGlobalAudit(filters?: GlobalAuditFilters, options?: { enabled?: boolean }) {
  return useInfiniteQuery<AuditPage>({
    queryKey: auditKeys.global(filters),
    enabled: options?.enabled ?? true,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/audit/global', {
        params: {
          query: {
            limit: DEFAULT_PAGE_LIMIT,
            ...(filters?.action ? { action: filters.action } : {}),
            ...(pageParam ? { cursor: pageParam as string } : {}),
          },
        },
      })
      if (error || !data) throw new Error('Failed to load global audit log')
      return data
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })
}
