// FindingsList — a table of the engagement's live findings. Each row shows a
// color-coded severity badge, the title, the inline verification + remediation
// status pickers (StatusPickers), the linked graph-node label (or "—"), and
// Edit/Delete actions. Loading shows a skeleton; empty shows a prompt.
//
// Linked-node labels are resolved via the existing graph snapshot (useGraph) so a
// finding anchored to a host/service/url reads naturally; if the node is gone the
// row falls back to "—".
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGraph } from '@/features/graph/api'
import { useDeleteFinding, useFindings } from '../api'
import type { Finding } from '../api'
import { SEVERITY_LABELS, SEVERITY_VARIANT } from '../findingsLabels'
import { StatusPickers } from './StatusPickers'

export interface FindingsListProps {
  engagementId: string
  /** Called when the user clicks Edit on a row — parent opens FindingDialog. */
  onEditFinding: (finding: Finding) => void
}

export function FindingsList({ engagementId, onEditFinding }: FindingsListProps) {
  const { data, isLoading, isError, error } = useFindings(engagementId)
  const graph = useGraph(engagementId)
  const deleteFinding = useDeleteFinding(engagementId)

  if (isLoading) {
    return (
      <div data-testid="findings-list-skeleton" className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load findings.'}
      </p>
    )
  }

  const findings = data?.items ?? []
  const nodeLabelById = new Map<string, string>(
    (graph.data?.nodes ?? []).map((n) => [n.id, n.label]),
  )

  if (findings.length === 0) {
    return <p className="text-sm text-muted-foreground">No findings yet — add one.</p>
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">Severity</th>
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">Title</th>
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">Status</th>
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">Node</th>
            <th className="px-4 py-3 text-right font-medium text-muted-foreground">Actions</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((finding) => (
            <tr key={finding.id} className="border-b last:border-0 align-top">
              <td className="px-4 py-3">
                <Badge variant={SEVERITY_VARIANT[finding.severity]}>
                  {SEVERITY_LABELS[finding.severity]}
                </Badge>
              </td>
              <td className="px-4 py-3 font-medium">{finding.title}</td>
              <td className="px-4 py-3">
                <StatusPickers engagementId={engagementId} finding={finding} />
              </td>
              <td className="px-4 py-3 text-muted-foreground">
                {finding.node_id ? (nodeLabelById.get(finding.node_id) ?? finding.node_id) : '—'}
              </td>
              <td className="px-4 py-3">
                <div className="flex items-center justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={() => onEditFinding(finding)}>
                    Edit
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleteFinding.isPending}
                    onClick={() => deleteFinding.mutate(finding.id)}
                  >
                    Delete
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
