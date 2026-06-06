import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ApprovalReason } from '@/shared/api'
import { useAutonomyGrants, useRevokeAutonomy } from '../api'

/** Category labels for an active standing-autonomy grant (the four delegable §5.2 reasons). */
const GRANT_LABELS: Record<string, string> = {
  target_write: 'Target writes',
  aggressive_scan: 'Aggressive scans',
  credential_attack: 'Credential attacks',
  out_of_scope: 'Out-of-scope commands',
}

function label(reason: ApprovalReason): string {
  return GRANT_LABELS[reason] ?? reason
}

interface AutonomyPanelProps {
  engagementId: string
}

/**
 * The Autonomy panel (§5.2 delegation): lists the engagement's ACTIVE standing-autonomy
 * grants — the categories whose future gated commands auto-approve without a human click —
 * each with the grantor, when, and a Revoke action. Revoking is immediate: the next gated
 * command of that category gates with a human card again. Membership-gated server-side.
 */
export function AutonomyPanel({ engagementId }: AutonomyPanelProps) {
  const { data: grants, isLoading, isError } = useAutonomyGrants(engagementId)
  const revoke = useRevokeAutonomy(engagementId)
  const active = grants ?? []

  return (
    <section aria-label="Autonomy" data-testid="autonomy-panel">
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">
        Standing autonomy{active.length > 0 ? ` (${active.length})` : ''}
      </h2>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading grants…</p> : null}
      {isError ? <p className="text-sm text-destructive">Failed to load grants.</p> : null}
      {!isLoading && !isError && active.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="autonomy-empty">
          No standing autonomy. Gated commands require a human approval.
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {active.map((grant) => (
          <li
            key={grant.id}
            data-testid="autonomy-grant"
            className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2"
          >
            <div className="min-w-0">
              <Badge variant="secondary">{label(grant.reason)}</Badge>
              <p className="mt-1 text-xs text-muted-foreground">
                granted by @{grant.granted_by_username ?? 'unknown'} · {grant.created_at.slice(0, 10)}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              data-testid={`revoke-${grant.id}`}
              onClick={() => revoke.mutate({ grantId: grant.id })}
              disabled={revoke.isPending}
            >
              Revoke
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}
