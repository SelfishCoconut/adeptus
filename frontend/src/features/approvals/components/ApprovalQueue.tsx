import { useApprovalRequests } from '../api'
import { ApprovalCard } from './ApprovalCard'

const POLL_INTERVAL_MS = 5000

interface ApprovalQueueProps {
  engagementId: string
}

/**
 * The per-engagement Approvals tab (§5.2, Resolved decision 4): the engagement-shared queue
 * of PENDING dangerous-command requests, visible to ALL members so a second member can act
 * without reading the initiator's private chat. Each row is an inline {@link ApprovalCard}
 * with Approve/Reject; a decision (here or in chat) invalidates the query so the acted-on
 * request drops out of the pending list. Polls so another member's decision shows up without
 * a manual refresh. (This is also the data source the Slice-32 notifications bell will consume.)
 */
export function ApprovalQueue({ engagementId }: ApprovalQueueProps) {
  const { data, isLoading, isError } = useApprovalRequests(engagementId, {
    status: 'pending',
    refetchInterval: POLL_INTERVAL_MS,
  })
  const requests = data?.items ?? []

  return (
    <section aria-label="Approvals" data-testid="approval-queue">
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">
        Approvals{requests.length > 0 ? ` (${requests.length})` : ''}
      </h2>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading approvals…</p> : null}
      {isError ? <p className="text-sm text-destructive">Failed to load approvals.</p> : null}
      {!isLoading && !isError && requests.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="approval-queue-empty">
          No pending approvals.
        </p>
      ) : null}
      <div className="flex flex-col gap-2">
        {requests.map((request) => (
          <ApprovalCard key={request.id} engagementId={engagementId} request={request} />
        ))}
      </div>
    </section>
  )
}
