import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import type { AuditAction, AuditEntry } from '@/shared/api'
import { useEngagementAudit } from '../api'

interface AuditLogTableProps {
  engagementId: string
}

// Exhaustive list of AuditAction values for the filter dropdown. Typed AuditAction[]
// so any non-existent value is a type error — but new actions added to the generated
// `AuditAction` union (e.g. Slice 11/16) must be appended here manually (a missing one
// is NOT a type error, only a missing dropdown option). Keep in sync with schema.ts.
const AUDIT_ACTIONS: AuditAction[] = [
  'login',
  'logout',
  'login_failed',
  'tool_run',
  'tool_run_completed',
  'graph_node_created',
  'graph_node_updated',
  'graph_node_deleted',
  'graph_edge_created',
  'graph_edge_deleted',
  'approval_granted',
  'approval_rejected',
  'ai_call',
]

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function shortId(id: string | null): string {
  return id ? id.slice(0, 8) : '—'
}

function SelfApprovedCell({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-muted-foreground">—</span>
  return value ? (
    <Badge variant="destructive">self</Badge>
  ) : (
    <Badge variant="secondary">cross</Badge>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-3 py-2 font-mono text-xs tabular-nums">{entry.seq}</td>
      <td className="px-3 py-2 whitespace-nowrap text-xs">{formatTime(entry.created_at)}</td>
      <td className="px-3 py-2">
        <Badge variant="outline">{entry.action}</Badge>
      </td>
      <td className="px-3 py-2 font-mono text-xs" title={entry.actor_user_id ?? 'system'}>
        {shortId(entry.actor_user_id)}
      </td>
      <td className="px-3 py-2 text-xs">
        {entry.target_type ? `${entry.target_type}:${shortId(entry.target_id)}` : '—'}
      </td>
      <td className="px-3 py-2">
        <SelfApprovedCell value={entry.self_approved} />
      </td>
    </tr>
  )
}

export function AuditLogTable({ engagementId }: AuditLogTableProps) {
  const [action, setAction] = useState<AuditAction | ''>('')
  const [selfApprovedOnly, setSelfApprovedOnly] = useState(false)

  const query = useEngagementAudit(engagementId, {
    action: action || undefined,
    selfApproved: selfApprovedOnly ? true : undefined,
  })

  const entries = query.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Action</span>
          <select
            aria-label="Filter by action"
            className="h-8 rounded-md border border-input bg-background px-2 text-sm"
            value={action}
            onChange={(e) => setAction(e.target.value as AuditAction | '')}
          >
            <option value="">All actions</option>
            {AUDIT_ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Switch
            aria-label="Self-approved only"
            checked={selfApprovedOnly}
            onCheckedChange={setSelfApprovedOnly}
          />
          <span className="text-muted-foreground">Self-approved only</span>
        </label>
      </div>

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading audit log…</p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Failed to load audit log.
        </p>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No audit entries.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Seq</th>
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium">Actor</th>
                <th className="px-3 py-2 font-medium">Target</th>
                <th className="px-3 py-2 font-medium">Self-approved</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <AuditRow key={entry.id} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {query.hasNextPage ? (
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </Button>
      ) : null}
    </div>
  )
}
