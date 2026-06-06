import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/shared/api'
import type { components } from '@/shared/api'

// ---------------------------------------------------------------------------
// Generated types — never hand-written
// ---------------------------------------------------------------------------

export type Finding = components['schemas']['Finding']
export type FindingList = components['schemas']['FindingList']
export type FindingCreate = components['schemas']['FindingCreate']
export type FindingUpdate = components['schemas']['FindingUpdate']
export type Severity = components['schemas']['Severity']
export type VerificationStatus = components['schemas']['VerificationStatus']
export type RemediationStatus = components['schemas']['RemediationStatus']

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const findingsKeys = {
  list: (engagementId: string, includeDeleted = false) =>
    ['findings', engagementId, { includeDeleted }] as const,
  detail: (engagementId: string, findingId: string) =>
    ['findings', engagementId, 'detail', findingId] as const,
} as const

// ---------------------------------------------------------------------------
// Error extraction — surfaces structured server errors (422, 409, etc.) so
// dialogs can display them to the user (mirrors the graph feature).
// ---------------------------------------------------------------------------

function extractServerMessage(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const e = error as Record<string, unknown>
  // FastAPI 422 detail is an array of validation errors.
  if (Array.isArray(e.detail)) {
    const first = e.detail[0] as Record<string, unknown> | undefined
    if (first && typeof first.msg === 'string') return first.msg
  }
  // Structured error body: { error: { code, message } }.
  if (typeof e.error === 'object' && e.error !== null) {
    const inner = e.error as Record<string, unknown>
    if (typeof inner.message === 'string') return inner.message
  }
  if (typeof e.detail === 'string') return e.detail
  return undefined
}

function toError(error: unknown, fallback: string): Error {
  return new Error(extractServerMessage(error) ?? fallback)
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/engagements/{engagement_id}/findings
 * Returns the engagement's findings (newest-first). When `includeDeleted` is
 * true, soft-deleted findings are included.
 */
export function useFindings(engagementId: string, includeDeleted = false) {
  return useQuery<FindingList>({
    queryKey: findingsKeys.list(engagementId, includeDeleted),
    enabled: !!engagementId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/findings',
        {
          params: {
            path: { engagement_id: engagementId },
            query: { include_deleted: includeDeleted },
          },
        },
      )
      if (error || !data) throw toError(error, 'Failed to load findings')
      return data
    },
  })
}

/**
 * GET /api/v1/engagements/{engagement_id}/findings/{finding_id}
 * Returns one finding's full detail.
 */
export function useFinding(engagementId: string, findingId: string) {
  return useQuery<Finding>({
    queryKey: findingsKeys.detail(engagementId, findingId),
    enabled: !!engagementId && !!findingId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/engagements/{engagement_id}/findings/{finding_id}',
        { params: { path: { engagement_id: engagementId, finding_id: findingId } } },
      )
      if (error || !data) throw toError(error, 'Failed to load finding')
      return data
    },
  })
}

// ---------------------------------------------------------------------------
// Mutations — each invalidates the findings list (and the detail where relevant).
// ---------------------------------------------------------------------------

function useInvalidateFindings(engagementId: string) {
  const queryClient = useQueryClient()
  return (findingId?: string) => {
    // Invalidate every list variant (include_deleted true/false) for this engagement.
    void queryClient.invalidateQueries({ queryKey: ['findings', engagementId] })
    if (findingId) {
      void queryClient.invalidateQueries({
        queryKey: findingsKeys.detail(engagementId, findingId),
      })
    }
  }
}

/** POST /api/v1/engagements/{engagement_id}/findings */
export function useCreateFinding(engagementId: string) {
  const invalidate = useInvalidateFindings(engagementId)
  return useMutation<Finding, Error, FindingCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST(
        '/api/v1/engagements/{engagement_id}/findings',
        { params: { path: { engagement_id: engagementId } }, body },
      )
      if (error || !data) throw toError(error, 'Failed to create finding')
      return data
    },
    onSuccess: (finding) => invalidate(finding.id),
  })
}

/** PATCH /api/v1/engagements/{engagement_id}/findings/{finding_id} */
export function useUpdateFinding(engagementId: string) {
  const invalidate = useInvalidateFindings(engagementId)
  return useMutation<Finding, Error, { findingId: string } & FindingUpdate>({
    mutationFn: async ({ findingId, ...body }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/engagements/{engagement_id}/findings/{finding_id}',
        { params: { path: { engagement_id: engagementId, finding_id: findingId } }, body },
      )
      if (error || !data) throw toError(error, 'Failed to update finding')
      return data
    },
    onSuccess: (finding) => invalidate(finding.id),
  })
}

/** PATCH /api/v1/engagements/{engagement_id}/findings/{finding_id}/verification */
export function useSetVerification(engagementId: string) {
  const invalidate = useInvalidateFindings(engagementId)
  return useMutation<
    Finding,
    Error,
    { findingId: string; verification_status: VerificationStatus }
  >({
    mutationFn: async ({ findingId, verification_status }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/engagements/{engagement_id}/findings/{finding_id}/verification',
        {
          params: { path: { engagement_id: engagementId, finding_id: findingId } },
          body: { verification_status },
        },
      )
      if (error || !data) throw toError(error, 'Failed to update verification status')
      return data
    },
    onSuccess: (finding) => invalidate(finding.id),
  })
}

/** PATCH /api/v1/engagements/{engagement_id}/findings/{finding_id}/remediation */
export function useSetRemediation(engagementId: string) {
  const invalidate = useInvalidateFindings(engagementId)
  return useMutation<
    Finding,
    Error,
    { findingId: string; remediation_status: RemediationStatus }
  >({
    mutationFn: async ({ findingId, remediation_status }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/engagements/{engagement_id}/findings/{finding_id}/remediation',
        {
          params: { path: { engagement_id: engagementId, finding_id: findingId } },
          body: { remediation_status },
        },
      )
      if (error || !data) throw toError(error, 'Failed to update remediation status')
      return data
    },
    onSuccess: (finding) => invalidate(finding.id),
  })
}

/** DELETE /api/v1/engagements/{engagement_id}/findings/{finding_id} (soft-delete) */
export function useDeleteFinding(engagementId: string) {
  const invalidate = useInvalidateFindings(engagementId)
  return useMutation<void, Error, string>({
    mutationFn: async (findingId) => {
      const { error } = await api.DELETE(
        '/api/v1/engagements/{engagement_id}/findings/{finding_id}',
        { params: { path: { engagement_id: engagementId, finding_id: findingId } } },
      )
      if (error) throw toError(error, 'Failed to delete finding')
    },
    onSuccess: () => invalidate(),
  })
}
