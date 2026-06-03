// useGraphPaneState — owns all of GraphPane's local UI state and handlers so
// the component itself stays a thin view composition. Tracks: current view
// (graph/list), the create/edit dialog, the canvas-selected node (by id,
// re-derived from live data so it reflects edits and clears on delete), and
// history visibility. Also runs pin reconciliation after each graph load.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useGraph } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'

export type GraphView = 'graph' | 'list'

export interface GraphPaneState {
  view: GraphView
  setView: (view: GraphView) => void
  dialogOpen: boolean
  dialogNode: Node | undefined
  selectedNode: Node | null
  showHistory: boolean
  toggleHistory: () => void
  handleAddNode: () => void
  handleEditNode: (node: Node) => void
  handleSelectNode: (node: Node | null) => void
  handleDialogOpenChange: (open: boolean) => void
  clearSelection: () => void
}

export function useGraphPaneState(engagementId: string): GraphPaneState {
  const { data } = useGraph(engagementId)
  const reconcile = usePinStore((s) => s.reconcile)

  const [view, setView] = useState<GraphView>('graph')

  // Dialog: undefined node = create mode; Node = edit mode.
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogNode, setDialogNode] = useState<Node | undefined>(undefined)

  // Canvas selection tracked by id, re-derived from live data.
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

  const handleDialogOpenChange = useCallback((open: boolean) => {
    setDialogOpen(open)
    // Default the next open to create mode rather than re-opening edit mode.
    if (!open) setDialogNode(undefined)
  }, [])

  const clearSelection = useCallback(() => setSelectedNodeId(null), [])

  const toggleHistory = useCallback(() => setShowHistory((prev) => !prev), [])

  return {
    view,
    setView,
    dialogOpen,
    dialogNode,
    selectedNode,
    showHistory,
    toggleHistory,
    handleAddNode,
    handleEditNode,
    handleSelectNode,
    handleDialogOpenChange,
    clearSelection,
  }
}
