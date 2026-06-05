import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGraph, useDeleteNode } from '../api'
import type { Node } from '../api'

// ---------------------------------------------------------------------------
// Badge colour per node type
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

export interface GraphNodeListProps {
  engagementId: string
  /** Called when the user clicks Edit on a row — parent opens NodeEditDialog. */
  onEditNode: (node: Node) => void
  /** Called when the user clicks "Add node" (toolbar affordance). */
  onAddNode: () => void
  /**
   * Optional per-node decorator slot (additive, read-only). The workspace passes the
   * Slice-13 certainty overlay here; undefined keeps the row exactly as before.
   */
  nodeAccessory?: (nodeId: string) => React.ReactNode
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphNodeList({
  engagementId,
  onEditNode,
  onAddNode,
  nodeAccessory,
}: GraphNodeListProps) {
  const { data, isLoading, isError, error } = useGraph(engagementId)
  const deleteNode = useDeleteNode(engagementId)

  // Loading state
  if (isLoading) {
    return (
      <div data-testid="graph-node-list-skeleton" className="flex flex-col gap-2">
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
        {error instanceof Error ? error.message : 'Failed to load graph.'}
      </p>
    )
  }

  const nodes = data?.nodes ?? []
  const edgeCount = data?.edges?.length ?? 0

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {nodes.length} node{nodes.length !== 1 ? 's' : ''},{' '}
          {edgeCount} edge{edgeCount !== 1 ? 's' : ''}
        </p>
        <Button size="sm" onClick={onAddNode}>
          Add node
        </Button>
      </div>

      {/* Empty state */}
      {nodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No graph entities yet — add one.
        </p>
      ) : (
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
              {nodes.map((node) => (
                <tr key={node.id} className="border-b last:border-0">
                  <td className="px-4 py-3">
                    <Badge variant={nodeTypeVariant(node.type)}>{node.type}</Badge>
                  </td>
                  <td className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-2">
                      {node.label}
                      {nodeAccessory?.(node.id)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onEditNode(node)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleteNode.isPending}
                        onClick={() => deleteNode.mutate(node.id)}
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
      )}
    </div>
  )
}
