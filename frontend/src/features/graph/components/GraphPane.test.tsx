import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GraphPane } from './GraphPane'
import { useGraph } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'

// ---------------------------------------------------------------------------
// Module mocks — mock the child components at the boundary; each has its own
// test. GraphCanvas exposes a button to drive node selection; SelectedNodePanel
// echoes its node id and surfaces Edit/Delete callbacks.
// ---------------------------------------------------------------------------

const SELECTED_NODE: Node = {
  id: 'node-1',
  engagement_id: '00000000-0000-0000-0000-000000000001',
  type: 'host',
  label: '10.0.0.1',
  properties: {},
  deleted: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

vi.mock('./GraphCanvas', () => ({
  GraphCanvas: ({ onSelectNode }: { onSelectNode: (n: Node | null) => void }) => (
    <div data-testid="graph-canvas">
      <button type="button" onClick={() => onSelectNode(SELECTED_NODE)}>
        Select node
      </button>
      <button type="button" onClick={() => onSelectNode(null)}>
        Deselect
      </button>
    </div>
  ),
}))

vi.mock('./SelectedNodePanel', () => ({
  SelectedNodePanel: ({
    node,
    onEdit,
    onDeleted,
  }: {
    node: Node
    onEdit: (n: Node) => void
    onDeleted?: () => void
  }) => (
    <div data-testid="selected-node-panel" data-node-id={node.id}>
      <button type="button" onClick={() => onEdit(node)}>
        Panel edit
      </button>
      <button type="button" onClick={() => onDeleted?.()}>
        Panel deleted
      </button>
    </div>
  ),
}))

vi.mock('./GraphNodeList', () => ({
  GraphNodeList: ({
    onAddNode,
    onEditNode,
  }: {
    onAddNode: () => void
    onEditNode: (node: Node) => void
  }) => (
    <div data-testid="graph-node-list">
      <button type="button" onClick={onAddNode}>
        List add node
      </button>
      <button type="button" onClick={() => onEditNode(SELECTED_NODE)}>
        List edit node
      </button>
    </div>
  ),
}))

vi.mock('./NodeEditDialog', () => ({
  NodeEditDialog: ({ open, node }: { open: boolean; node?: Node }) =>
    open ? (
      <div
        data-testid="node-edit-dialog"
        data-mode={node ? 'edit' : 'create'}
        data-node-id={node?.id ?? ''}
      />
    ) : null,
}))

vi.mock('./GraphHistoryPanel', () => ({
  GraphHistoryPanel: ({ engagementId }: { engagementId: string }) => (
    <div data-testid="graph-history-panel" data-engagement-id={engagementId} />
  ),
}))

vi.mock('../api', () => ({
  useGraph: vi.fn(),
}))

const mockedUseGraph = vi.mocked(useGraph)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function graphResult(nodes: Node[] = [SELECTED_NODE]) {
  return {
    data: { nodes, edges: [] },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useGraph>
}

function renderPane() {
  return render(<GraphPane engagementId={ENGAGEMENT_ID} />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphPane', () => {
  beforeEach(() => {
    mockedUseGraph.mockReset()
    mockedUseGraph.mockReturnValue(graphResult())
    localStorage.clear()
    usePinStore.setState({ pinnedByEngagement: {} })
  })

  it('test_renders_graph_canvas_by_default', () => {
    renderPane()
    expect(screen.getByTestId('graph-canvas')).toBeInTheDocument()
    expect(screen.queryByTestId('graph-node-list')).not.toBeInTheDocument()
  })

  it('test_list_graph_view_toggle', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'List' }))
    expect(screen.getByTestId('graph-node-list')).toBeInTheDocument()
    expect(screen.queryByTestId('graph-canvas')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Graph' }))
    expect(screen.getByTestId('graph-canvas')).toBeInTheDocument()
    expect(screen.queryByTestId('graph-node-list')).not.toBeInTheDocument()
  })

  it('test_add_node_opens_dialog', async () => {
    const user = userEvent.setup()
    renderPane()

    expect(screen.queryByTestId('node-edit-dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add node' }))

    const dialog = screen.getByTestId('node-edit-dialog')
    expect(dialog).toHaveAttribute('data-mode', 'create')
  })

  it('test_selecting_node_shows_selected_panel', async () => {
    const user = userEvent.setup()
    renderPane()

    expect(screen.queryByTestId('selected-node-panel')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Select node' }))
    const panel = screen.getByTestId('selected-node-panel')
    expect(panel).toHaveAttribute('data-node-id', 'node-1')

    // Deselect hides it.
    await user.click(screen.getByRole('button', { name: 'Deselect' }))
    expect(screen.queryByTestId('selected-node-panel')).not.toBeInTheDocument()
  })

  it('opens the dialog in edit mode from the selected node panel', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Select node' }))
    await user.click(screen.getByRole('button', { name: 'Panel edit' }))

    const dialog = screen.getByTestId('node-edit-dialog')
    expect(dialog).toHaveAttribute('data-mode', 'edit')
    expect(dialog).toHaveAttribute('data-node-id', 'node-1')
  })

  it('hides the selected panel when the node is deleted', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Select node' }))
    expect(screen.getByTestId('selected-node-panel')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Panel deleted' }))
    expect(screen.queryByTestId('selected-node-panel')).not.toBeInTheDocument()
  })

  it('opens the dialog in edit mode from the list view', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'List' }))
    await user.click(screen.getByRole('button', { name: 'List edit node' }))

    const dialog = screen.getByTestId('node-edit-dialog')
    expect(dialog).toHaveAttribute('data-mode', 'edit')
  })

  it('test_history_toggle_shows_panel', async () => {
    const user = userEvent.setup()
    renderPane()

    expect(screen.queryByTestId('graph-history-panel')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show history' }))
    expect(screen.getByTestId('graph-history-panel')).toHaveAttribute(
      'data-engagement-id',
      ENGAGEMENT_ID,
    )

    await user.click(screen.getByRole('button', { name: 'Hide history' }))
    expect(screen.queryByTestId('graph-history-panel')).not.toBeInTheDocument()
  })

  it('reconciles pins against live node ids after load', () => {
    // Pre-pin a stale node id that is not in the live graph.
    usePinStore.setState({ pinnedByEngagement: { [ENGAGEMENT_ID]: ['node-1', 'ghost'] } })
    mockedUseGraph.mockReturnValue(graphResult([SELECTED_NODE])) // only node-1 lives

    renderPane()

    expect(usePinStore.getState().pinnedNodeIds(ENGAGEMENT_ID)).toEqual(['node-1'])
  })
})
