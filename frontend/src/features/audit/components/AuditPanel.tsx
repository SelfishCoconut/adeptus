import { AuditLogTable } from './AuditLogTable'

interface AuditPanelProps {
  engagementId: string
}

/**
 * Collapsible audit-log panel for the open engagement. The admin gate lives at the
 * call site (WorkspaceShell) — this component is only mounted for admins.
 */
export function AuditPanel({ engagementId }: AuditPanelProps) {
  return (
    <details className="rounded-md border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">Audit log</summary>
      <div className="mt-3">
        <AuditLogTable engagementId={engagementId} />
      </div>
    </details>
  )
}
