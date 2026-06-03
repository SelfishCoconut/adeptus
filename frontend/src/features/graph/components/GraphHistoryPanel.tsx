import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGraphHistory, useUndoNode } from '../api'

// ---------------------------------------------------------------------------
// Badge colour per node type (mirrors GraphNodeList)
// ---------------------------------------------------------------------------

const NODE_TYPE_VARIANT: Record<
  string,
  'default' | 'secondary' | 'outline' | 'destructive'
> = {
  host: 'default',
  port: 'secondary',
  service: 'secondary',
  url: 'outline',
  endpoint: 'outline',
  vulnerability: 'destructive',
  credential: 'destructive',
  note: 'secondary',
  attack_path: 'destructive',
}

function nodeTypeVariant(
  type: string,
): 'default' | 'secondary' | 'outline' | 'destructive' {
  return NODE_TYPE_VARIANT[type] ?? 'outline'
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphHistoryPanelProps {
  engagementId: string
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphHistoryPanel({ engagementId }: GraphHistoryPanelProps) {
  const { data, isLoading, isError, error } = useGraphHistory(engagementId)
  const undoNode = useUndoNode(engagementId)

  // Loading state
  if (isLoading) {
    return (
      <div data-testid="graph-history-panel-skeleton" className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  // Error state
  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load graph history.'}
      </p>
    )
  }

  const deletedNodes = data?.deleted_nodes ?? []

  // Empty state
  if (deletedNodes.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No deleted entities.</p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">
              Type
            </th>
            <th className="px-4 py-3 text-left font-medium text-muted-foreground">
              Label
            </th>
            <th className="px-4 py-3 text-right font-medium text-muted-foreground">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {deletedNodes.map((node) => (
            <tr key={node.id} className="border-b last:border-0">
              <td className="px-4 py-3">
                <Badge variant={nodeTypeVariant(node.type)}>{node.type}</Badge>
              </td>
              <td className="px-4 py-3 font-medium">{node.label}</td>
              <td className="px-4 py-3">
                <div className="flex items-center justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={undoNode.isPending}
                    onClick={() => undoNode.mutate(node.id)}
                  >
                    Undo
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
