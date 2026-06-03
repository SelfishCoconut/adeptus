// GraphPane — orchestrates GraphNodeList, NodeEditDialog, and GraphHistoryPanel.
// Mounted in the workspace shell's Graph section.  Manages local UI state only:
// which dialog is open, which node is selected for editing, and whether the
// History sub-panel is visible.  All server state lives in the child components'
// TanStack Query hooks.
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { GraphNodeList } from './GraphNodeList'
import { NodeEditDialog } from './NodeEditDialog'
import { GraphHistoryPanel } from './GraphHistoryPanel'
import type { Node } from '../api'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphPaneProps {
  engagementId: string
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphPane({ engagementId }: GraphPaneProps) {
  // Dialog state — null means closed; undefined node = create mode; Node = edit mode.
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<Node | undefined>(undefined)

  // History sub-panel toggle.
  const [showHistory, setShowHistory] = useState(false)

  function handleAddNode() {
    setSelectedNode(undefined)
    setDialogOpen(true)
  }

  function handleEditNode(node: Node) {
    setSelectedNode(node)
    setDialogOpen(true)
  }

  function handleDialogOpenChange(open: boolean) {
    setDialogOpen(open)
    if (!open) {
      // Clear selection when dialog is dismissed so the next open defaults to
      // create mode rather than accidentally re-opening edit mode.
      setSelectedNode(undefined)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Node list + Add node button (Add node toolbar affordance lives in
          GraphNodeList, but we also control the dialog from here). */}
      <GraphNodeList
        engagementId={engagementId}
        onAddNode={handleAddNode}
        onEditNode={handleEditNode}
      />

      {/* History toggle */}
      <div className="flex items-center justify-between border-t pt-3">
        <span className="text-sm font-medium text-muted-foreground">
          Deleted entities
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowHistory((prev) => !prev)}
          aria-expanded={showHistory}
        >
          {showHistory ? 'Hide history' : 'Show history'}
        </Button>
      </div>

      {showHistory && (
        <GraphHistoryPanel engagementId={engagementId} />
      )}

      {/* Node create/edit dialog */}
      <NodeEditDialog
        engagementId={engagementId}
        open={dialogOpen}
        onOpenChange={handleDialogOpenChange}
        node={selectedNode}
      />
    </div>
  )
}
