// SelectedNodePanel — inspector for the node currently selected on the canvas.
//
// Shows the node's type / label / properties and surfaces the Slice 07 action
// surface (no new mutation hooks): Pin/Unpin (pin store), Edit (opens the
// existing NodeEditDialog via the parent), Delete (useDeleteNode), and Undo
// (useUndoNode) — Undo is shown only when the node has prior history to revert
// to (it has been updated since creation).
import { useMemo } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useDeleteNode, useUndoNode } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SelectedNodePanelProps {
  engagementId: string
  node: Node
  /** Open the shared NodeEditDialog in edit mode for this node. */
  onEdit: (node: Node) => void
  /** Called after the node is deleted so the parent can clear the selection. */
  onDeleted?: () => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SelectedNodePanel({
  engagementId,
  node,
  onEdit,
  onDeleted,
}: SelectedNodePanelProps) {
  // Select the stable map reference (same pattern as GraphCanvas) and derive
  // the boolean, rather than computing it inside the selector.
  const pinnedByEngagement = usePinStore((s) => s.pinnedByEngagement)
  const togglePin = usePinStore((s) => s.togglePin)
  const isPinned = useMemo(
    () => pinnedByEngagement[engagementId]?.includes(node.id) ?? false,
    [pinnedByEngagement, engagementId, node.id],
  )

  const deleteNode = useDeleteNode(engagementId)
  const undoNode = useUndoNode(engagementId)

  // A node has something to undo only once it has been modified after creation.
  const canUndo = node.updated_at !== node.created_at

  const properties = node.properties ?? {}
  const hasProperties = Object.keys(properties).length > 0

  function handleDelete() {
    deleteNode.mutate(node.id, { onSuccess: () => onDeleted?.() })
  }

  return (
    <div
      data-testid="selected-node-panel"
      aria-label="Selected node"
      className="flex flex-col gap-3 rounded-md border p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="outline">{node.type}</Badge>
          <span className="font-medium">{node.label}</span>
          {isPinned && (
            <Badge variant="secondary" data-testid="pinned-badge">
              Pinned
            </Badge>
          )}
        </div>
      </div>

      {/* Properties */}
      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium text-muted-foreground">Properties</span>
        {hasProperties ? (
          <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-xs">
            {JSON.stringify(properties, null, 2)}
          </pre>
        ) : (
          <span className="text-xs text-muted-foreground">No properties</span>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={isPinned ? 'secondary' : 'outline'}
          size="sm"
          aria-pressed={isPinned}
          onClick={() => togglePin(engagementId, node.id)}
        >
          {isPinned ? 'Unpin' : 'Pin'}
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(node)}>
          Edit
        </Button>
        <Button
          variant="destructive"
          size="sm"
          disabled={deleteNode.isPending}
          onClick={handleDelete}
        >
          Delete
        </Button>
        {canUndo && (
          <Button
            variant="outline"
            size="sm"
            disabled={undoNode.isPending}
            onClick={() => undoNode.mutate(node.id)}
          >
            Undo
          </Button>
        )}
      </div>

      {(deleteNode.error || undoNode.error) && (
        <p role="alert" className="text-sm text-destructive">
          {(deleteNode.error ?? undoNode.error)?.message}
        </p>
      )}
    </div>
  )
}
