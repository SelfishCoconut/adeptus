import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ApprovalReason, ApprovalRequest, DelegableReason } from '@/shared/api'
import { useGrantAutonomy } from '@/features/autonomy/api'
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
  out_of_scope: 'target is outside the declared scope',
}

/** Category labels for the "Always allow <category>" grant action (plural phrasing). */
const GRANT_LABELS: Record<DelegableReason, string> = {
  target_write: 'target writes',
  aggressive_scan: 'aggressive scans',
  credential_attack: 'credential attacks',
  out_of_scope: 'out-of-scope commands',
}

/** The four delegable §5.2 categories — `unclassified_manifest` is never delegable. */
const DELEGABLE_REASONS: DelegableReason[] = [
  'target_write',
  'aggressive_scan',
  'credential_attack',
  'out_of_scope',
]

function isDelegable(reason: ApprovalReason): reason is DelegableReason {
  return (DELEGABLE_REASONS as string[]).includes(reason)
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
          <Badge variant="secondary" data-testid="autonomous-badge">
            {autonomous.auto_approved ? 'auto-approved · standing autonomy' : 'running automatically'}
          </Badge>
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
  const grant = useGrantAutonomy(engagementId)

  // The latest decision wins: a successful mutation here, else another member's decision that
  // arrived on a refetch of the `request` prop.
  const decided = approve.data ?? reject.data ?? request
  const isPending = decided.status === 'pending'
  const inFlight = approve.isPending || reject.isPending || grant.isPending

  // "Always allow" is offered per delegable category, but ONLY when the command's reasons are
  // ALL delegable: a card carrying `unclassified_manifest` can never be made to auto-run
  // (that fail-safe always gates, §5.2), so advertising delegation there would be dishonest.
  const delegableReasons = decided.reasons.every(isDelegable) ? decided.reasons.filter(isDelegable) : []

  // Grant standing autonomy for the category, then approve the current (already-pending)
  // request — a grant only auto-approves FUTURE turns, so this command still needs the click.
  function alwaysAllow(reason: DelegableReason) {
    grant.mutate(
      { reason },
      { onSuccess: () => approve.mutate({ requestId: request.id }) },
    )
  }
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
      {decided.reasons.includes('out_of_scope') && decided.out_of_scope_host ? (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400" data-testid="scope-context">
          <span className="font-mono">{decided.out_of_scope_host}</span> is not in scope:{' '}
          <span className="font-mono">{decided.scope_checked_against ?? '(scope not recorded)'}</span>
        </p>
      ) : null}
      {request.rationale ? (
        <p className="mt-1 text-xs text-muted-foreground">{request.rationale}</p>
      ) : null}

      {isPending ? (
        <>
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
          {delegableReasons.length > 0 ? (
            <div className="mt-2 flex flex-wrap items-center gap-2" data-testid="always-allow-row">
              {delegableReasons.map((reason) => (
                <AlwaysAllowButton
                  key={reason}
                  reason={reason}
                  disabled={inFlight}
                  onConfirm={alwaysAllow}
                />
              ))}
            </div>
          ) : null}
          {grant.isError ? (
            <p className="mt-1 text-xs text-destructive" data-testid="grant-error">
              Couldn&apos;t grant standing autonomy
            </p>
          ) : null}
        </>
      ) : (
        <p className="mt-2 text-xs font-medium text-foreground" data-testid="approval-decision">
          {decided.status === 'approved' ? 'Approved' : 'Rejected'} by @
          {decided.acted_by_username ?? 'unknown'}
        </p>
      )}
    </div>
  )
}

/**
 * The "Always allow <category> for this engagement" grant action (§5.2 delegation). For
 * `out_of_scope` it gates behind a louder explicit confirm (Risk 2) — granting it lets the
 * AI act outside the declared scope without asking. `onConfirm` grants then approves.
 */
function AlwaysAllowButton({
  reason,
  disabled,
  onConfirm,
}: {
  reason: DelegableReason
  disabled: boolean
  onConfirm: (reason: DelegableReason) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const louder = reason === 'out_of_scope'

  if (louder && confirming) {
    return (
      <div
        data-testid="out-of-scope-confirm"
        className="flex flex-wrap items-center gap-2 rounded border border-destructive/50 bg-destructive/5 px-2 py-1"
      >
        <span className="text-xs text-destructive">
          You are granting <strong>out-of-scope</strong> autonomy — the AI may act outside the
          declared scope without asking.
        </span>
        <Button
          size="sm"
          variant="destructive"
          data-testid="out-of-scope-confirm-grant"
          onClick={() => onConfirm(reason)}
          disabled={disabled}
        >
          Grant anyway
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={disabled}>
          Cancel
        </Button>
      </div>
    )
  }

  return (
    <Button
      size="sm"
      variant="outline"
      data-testid={`always-allow-${reason}`}
      onClick={() => (louder ? setConfirming(true) : onConfirm(reason))}
      disabled={disabled}
    >
      Always allow {GRANT_LABELS[reason]} for this engagement
    </Button>
  )
}
