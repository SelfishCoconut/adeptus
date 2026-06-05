import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ApprovalReason, ApprovalRequest } from '@/shared/api'
import {
  ApprovalConflictError,
  useApproveRequest,
  useRejectRequest,
  type AutonomousAction,
} from '../api'

/** Human-readable labels for the §5.2 dangerous categories + the escape hatch. */
const REASON_LABELS: Record<ApprovalReason, string> = {
  target_write: 'modifies target',
  aggressive_scan: 'aggressive scan',
  credential_attack: 'credential attack',
  unclassified_manifest: 'tool not classified in its manifest',
  out_of_scope: 'outside the scope list',
}

function CommandSummary({
  server,
  tool,
  args,
  preset,
}: {
  server: string
  tool: string
  args: Record<string, unknown>
  preset?: string | null
}) {
  return (
    <div className="font-mono text-xs text-foreground">
      <span className="font-semibold">{server}</span>
      <span className="text-muted-foreground"> / </span>
      <span className="font-semibold">{tool}</span>
      {preset ? <span className="text-muted-foreground"> ({preset})</span> : null}
      <pre className="mt-1 overflow-x-auto rounded bg-muted px-2 py-1 text-muted-foreground">
        {JSON.stringify(args, null, 2)}
      </pre>
    </div>
  )
}

interface ApprovalCardProps {
  engagementId: string
  /** A gated request (renders the approve/reject card). */
  request?: ApprovalRequest
  /** An autonomous command running now (renders the "running automatically" variant). */
  autonomous?: AutonomousAction
}

/**
 * The inline command card (§5.2). For a gated `request` it shows the command, why it was
 * gated, and Approve/Reject while pending; once decided (here or by another member, after a
 * refetch) it shows "Approved/Rejected by @user" with the buttons gone. For an `autonomous`
 * command it shows the "running automatically" variant with no gate. Args are verbatim (§5.5).
 */
export function ApprovalCard({ engagementId, request, autonomous }: ApprovalCardProps) {
  if (autonomous) {
    return (
      <div
        data-testid="autonomous-card"
        className="rounded-lg border border-border bg-muted/40 p-3"
      >
        <div className="mb-1.5 flex items-center gap-2">
          <Badge variant="secondary">running automatically</Badge>
          {autonomous.rationale ? (
            <span className="text-xs text-muted-foreground">{autonomous.rationale}</span>
          ) : null}
        </div>
        <CommandSummary
          server={autonomous.server_name}
          tool={autonomous.tool_name}
          args={autonomous.args}
          preset={autonomous.preset_name}
        />
      </div>
    )
  }
  if (!request) return null
  return <GatedCard engagementId={engagementId} request={request} />
}

function GatedCard({ engagementId, request }: { engagementId: string; request: ApprovalRequest }) {
  const approve = useApproveRequest(engagementId)
  const reject = useRejectRequest(engagementId)

  // The latest decision wins: a successful mutation here, else another member's decision that
  // arrived on a refetch of the `request` prop.
  const decided = approve.data ?? reject.data ?? request
  const isPending = decided.status === 'pending'
  const inFlight = approve.isPending || reject.isPending
  const conflict =
    approve.error instanceof ApprovalConflictError
      ? approve.error
      : reject.error instanceof ApprovalConflictError
        ? reject.error
        : null

  return (
    <div data-testid="approval-card" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <Badge variant="destructive">needs approval</Badge>
        {decided.reasons.map((reason) => (
          <Badge key={reason} variant="outline" data-testid="reason-badge">
            {REASON_LABELS[reason]}
          </Badge>
        ))}
      </div>
      <CommandSummary
        server={decided.server_name}
        tool={decided.tool_name}
        args={decided.args}
        preset={decided.preset_name}
      />
      {request.rationale ? (
        <p className="mt-1 text-xs text-muted-foreground">{request.rationale}</p>
      ) : null}

      {isPending ? (
        <div className="mt-2 flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => approve.mutate({ requestId: request.id })}
            disabled={inFlight}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => reject.mutate({ requestId: request.id })}
            disabled={inFlight}
          >
            Reject
          </Button>
          {conflict ? (
            <span className="text-xs text-muted-foreground" data-testid="approval-conflict">
              {conflict.reason === 'engagement_archived'
                ? 'Engagement is archived'
                : `Already ${conflict.status ?? 'decided'} by another member`}
            </span>
          ) : null}
        </div>
      ) : (
        <p className="mt-2 text-xs font-medium text-foreground" data-testid="approval-decision">
          {decided.status === 'approved' ? 'Approved' : 'Rejected'} by @
          {decided.acted_by_username ?? 'unknown'}
        </p>
      )}
    </div>
  )
}
