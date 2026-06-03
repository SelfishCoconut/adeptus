// GraphPane — orchestrates the live graph surface in the workspace Graph
// region. Canvas-first (slice 08): the force-directed GraphCanvas is the
// primary view, with a SelectedNodePanel inspector for the tapped node. A
// "List / Graph" toggle retains the Slice 07 keyboard-accessible GraphNodeList
// (accessibility + existing selectors survive — Risk 5). The Add-node /
// NodeEditDialog (create + edit) and the "Show history" -> GraphHistoryPanel
// surfaces are reused from Slice 07 unchanged.
//
// Manages local UI state only: current view, dialog open/target, the selected
// canvas node, and history visibility. Server state lives in TanStack Query.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { GraphCanvas } from './GraphCanvas'
import { SelectedNodePanel } from './SelectedNodePanel'
import { GraphNodeList } from './GraphNodeList'
import { NodeEditDialog } from './NodeEditDialog'
import { GraphHistoryPanel } from './GraphHistoryPanel'
import { useGraph } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphPaneProps {
  engagementId: string
}

type GraphView = 'graph' | 'list'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphPane({ engagementId }: GraphPaneProps) {
  const { data } = useGraph(engagementId)
  const reconcile = usePinStore((s) => s.reconcile)

  const [view, setView] = useState<GraphView>('graph')

  // Dialog state — null/undefined node = create mode; Node = edit mode.
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogNode, setDialogNode] = useState<Node | undefined>(undefined)

  // Canvas selection is tracked by id and re-derived from live data so the
  // panel reflects edits and disappears when the node is deleted.
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const selectedNode = useMemo(
    () => data?.nodes?.find((n) => n.id === selectedNodeId) ?? null,
    [data, selectedNodeId],
  )

  const [showHistory, setShowHistory] = useState(false)

  // Drop pins for nodes that no longer exist after each successful graph load.
  useEffect(() => {
    if (data?.nodes) {
      reconcile(
        engagementId,
        data.nodes.map((n) => n.id),
      )
    }
  }, [data, engagementId, reconcile])

  const handleAddNode = useCallback(() => {
    setDialogNode(undefined)
    setDialogOpen(true)
  }, [])

  const handleEditNode = useCallback((node: Node) => {
    setDialogNode(node)
    setDialogOpen(true)
  }, [])

  const handleSelectNode = useCallback((node: Node | null) => {
    setSelectedNodeId(node?.id ?? null)
  }, [])

  function handleDialogOpenChange(open: boolean) {
    setDialogOpen(open)
    if (!open) {
      // Default the next open to create mode rather than re-opening edit mode.
      setDialogNode(undefined)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: view toggle + Add node */}
      <div className="flex items-center justify-between">
        <div role="group" aria-label="Graph view" className="flex items-center gap-1">
          <Button
            variant={view === 'graph' ? 'default' : 'outline'}
            size="sm"
            aria-pressed={view === 'graph'}
            onClick={() => setView('graph')}
          >
            Graph
          </Button>
          <Button
            variant={view === 'list' ? 'default' : 'outline'}
            size="sm"
            aria-pressed={view === 'list'}
            onClick={() => setView('list')}
          >
            List
          </Button>
        </div>
        {view === 'graph' && (
          <Button size="sm" onClick={handleAddNode}>
            Add node
          </Button>
        )}
      </div>

      {/* Primary surface */}
      {view === 'graph' ? (
        <>
          <GraphCanvas engagementId={engagementId} onSelectNode={handleSelectNode} />
          {selectedNode && (
            <SelectedNodePanel
              engagementId={engagementId}
              node={selectedNode}
              onEdit={handleEditNode}
              onDeleted={() => setSelectedNodeId(null)}
            />
          )}
        </>
      ) : (
        <GraphNodeList
          engagementId={engagementId}
          onAddNode={handleAddNode}
          onEditNode={handleEditNode}
        />
      )}

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

      {showHistory && <GraphHistoryPanel engagementId={engagementId} />}

      {/* Node create/edit dialog */}
      <NodeEditDialog
        engagementId={engagementId}
        open={dialogOpen}
        onOpenChange={handleDialogOpenChange}
        node={dialogNode}
      />
    </div>
  )
}
