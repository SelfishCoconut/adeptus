import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GraphPane } from './GraphPane'
import type { Node } from '../api'

// ---------------------------------------------------------------------------
// Module mocks — keep the workspace shell thin by mocking the child components
// at the module boundary. Each child's own test file covers it in depth.
// ---------------------------------------------------------------------------

vi.mock('./GraphNodeList', () => ({
  GraphNodeList: ({
    onAddNode,
    onEditNode,
  }: {
    engagementId: string
    onAddNode: () => void
    onEditNode: (node: Node) => void
  }) => (
    <div data-testid="graph-node-list">
      <button type="button" onClick={onAddNode}>
        Add node
      </button>
      <button
        type="button"
        onClick={() =>
          onEditNode({
            id: 'node-1',
            engagement_id: '00000000-0000-0000-0000-000000000001',
            type: 'host',
            label: '10.0.0.1',
            properties: {},
            deleted: false,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          })
        }
      >
        Edit node
      </button>
    </div>
  ),
}))

vi.mock('./NodeEditDialog', () => ({
  NodeEditDialog: ({
    open,
    node,
  }: {
    engagementId: string
    open: boolean
    onOpenChange: (open: boolean) => void
    node?: Node
  }) =>
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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function renderPane() {
  return render(<GraphPane engagementId={ENGAGEMENT_ID} />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphPane', () => {
  beforeEach(() => {
    // mocks are module-level; no per-test reset needed here.
  })

  it('renders the node list', () => {
    renderPane()
    expect(screen.getByTestId('graph-node-list')).toBeInTheDocument()
  })

  it('does not render the dialog initially', () => {
    renderPane()
    expect(screen.queryByTestId('node-edit-dialog')).not.toBeInTheDocument()
  })

  it('opens the dialog in create mode when "Add node" is clicked', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Add node' }))

    const dialog = screen.getByTestId('node-edit-dialog')
    expect(dialog).toBeInTheDocument()
    expect(dialog).toHaveAttribute('data-mode', 'create')
  })

  it('opens the dialog in edit mode when onEditNode is called', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Edit node' }))

    const dialog = screen.getByTestId('node-edit-dialog')
    expect(dialog).toBeInTheDocument()
    expect(dialog).toHaveAttribute('data-mode', 'edit')
    expect(dialog).toHaveAttribute('data-node-id', 'node-1')
  })

  it('does not render the history panel initially', () => {
    renderPane()
    expect(screen.queryByTestId('graph-history-panel')).not.toBeInTheDocument()
  })

  it('shows the history panel when "Show history" is clicked', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Show history' }))

    expect(screen.getByTestId('graph-history-panel')).toBeInTheDocument()
    expect(screen.getByTestId('graph-history-panel')).toHaveAttribute(
      'data-engagement-id',
      ENGAGEMENT_ID,
    )
  })

  it('hides the history panel when "Hide history" is clicked after showing it', async () => {
    const user = userEvent.setup()
    renderPane()

    await user.click(screen.getByRole('button', { name: 'Show history' }))
    expect(screen.getByTestId('graph-history-panel')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Hide history' }))
    expect(screen.queryByTestId('graph-history-panel')).not.toBeInTheDocument()
  })

  it('toggles the aria-expanded attribute on the history button', async () => {
    const user = userEvent.setup()
    renderPane()

    const btn = screen.getByRole('button', { name: 'Show history' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')

    await user.click(btn)
    expect(screen.getByRole('button', { name: 'Hide history' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })
})
