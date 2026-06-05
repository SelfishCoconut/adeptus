// GraphPane — orchestrates the live graph surface in the workspace Graph
// region. Canvas-first (slice 08): the force-directed GraphCanvas is the
// primary view, with a SelectedNodePanel inspector for the tapped node. A
// "List / Graph" toggle retains the Slice 07 keyboard-accessible GraphNodeList
// (accessibility + existing selectors survive — Risk 5). The Add-node /
// NodeEditDialog (create + edit) and the "Show history" -> GraphHistoryPanel
// surfaces are reused from Slice 07 unchanged.
//
// All local UI state + handlers live in useGraphPaneState so this component is
// a thin view composition; server state lives in TanStack Query.
import { Button } from '@/components/ui/button'
import { GraphCanvas } from './GraphCanvas'
import { SelectedNodePanel } from './SelectedNodePanel'
import { GraphNodeList } from './GraphNodeList'
import { NodeEditDialog } from './NodeEditDialog'
import { GraphHistoryPanel } from './GraphHistoryPanel'
import { UndoButton } from './UndoButton'
import { useGraphPaneState } from '../hooks/useGraphPaneState'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphPaneProps {
  engagementId: string
  /**
   * Optional per-node decorator slot (additive, read-only). The workspace supplies the
   * Slice-13 certainty overlay; it is rendered on both the list rows and the selected-node
   * inspector. Undefined leaves the Slice-08 pane unchanged.
   */
  nodeAccessory?: (nodeId: string) => React.ReactNode
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphPane({ engagementId, nodeAccessory }: GraphPaneProps) {
  const {
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
  } = useGraphPaneState(engagementId)

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
        <div className="flex items-center gap-2">
          {/* Personal undo stack (Slice 09): pop my last write. The Ctrl/Cmd+Z
              shortcut is suppressed while the node dialog is open so it doesn't
              hijack text editing. */}
          <UndoButton engagementId={engagementId} shortcutDisabled={dialogOpen} />
          {view === 'graph' && (
            <Button size="sm" onClick={handleAddNode}>
              Add node
            </Button>
          )}
        </div>
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
              onDeleted={clearSelection}
              accessory={nodeAccessory?.(selectedNode.id)}
            />
          )}
        </>
      ) : (
        <GraphNodeList
          engagementId={engagementId}
          onAddNode={handleAddNode}
          onEditNode={handleEditNode}
          nodeAccessory={nodeAccessory}
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
          onClick={toggleHistory}
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
