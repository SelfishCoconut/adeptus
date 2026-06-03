import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SelectedNodePanel } from './SelectedNodePanel'
import { useDeleteNode, useUndoNode } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'

vi.mock('../api', () => ({
  useDeleteNode: vi.fn(),
  useUndoNode: vi.fn(),
}))

const mockedUseDeleteNode = vi.mocked(useDeleteNode)
const mockedUseUndoNode = vi.mocked(useUndoNode)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeNode(overrides: Partial<Node> = {}): Node {
  return {
    id: 'node-a',
    engagement_id: ENGAGEMENT_ID,
    type: 'host',
    label: '10.0.0.1',
    properties: {},
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function mutationResult(overrides: Record<string, unknown> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useDeleteNode>
}

function renderPanel(
  props: {
    node?: Node
    onEdit?: (node: Node) => void
    onDeleted?: () => void
  } = {},
) {
  return render(
    <SelectedNodePanel
      engagementId={ENGAGEMENT_ID}
      node={props.node ?? makeNode()}
      onEdit={props.onEdit ?? vi.fn()}
      onDeleted={props.onDeleted}
    />,
  )
}

describe('SelectedNodePanel', () => {
  beforeEach(() => {
    mockedUseDeleteNode.mockReset()
    mockedUseUndoNode.mockReset()
    mockedUseDeleteNode.mockReturnValue(mutationResult())
    mockedUseUndoNode.mockReturnValue(
      mutationResult() as unknown as ReturnType<typeof useUndoNode>,
    )
    localStorage.clear()
    usePinStore.setState({ pinnedByEngagement: {} })
  })

  it('shows the node type, label and properties', () => {
    renderPanel({ node: makeNode({ label: 'web-01', properties: { os: 'linux' } }) })
    expect(screen.getByText('web-01')).toBeInTheDocument()
    expect(screen.getByText('host')).toBeInTheDocument()
    expect(screen.getByText(/"os": "linux"/)).toBeInTheDocument()
  })

  it('test_pin_toggle_updates_store', async () => {
    const user = userEvent.setup()
    renderPanel({ node: makeNode({ id: 'node-a' }) })

    expect(usePinStore.getState().isPinned(ENGAGEMENT_ID, 'node-a')).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Pin' }))
    expect(usePinStore.getState().isPinned(ENGAGEMENT_ID, 'node-a')).toBe(true)
    // Button now offers Unpin and the pinned badge appears.
    expect(screen.getByRole('button', { name: 'Unpin' })).toBeInTheDocument()
    expect(screen.getByTestId('pinned-badge')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Unpin' }))
    expect(usePinStore.getState().isPinned(ENGAGEMENT_ID, 'node-a')).toBe(false)
  })

  it('test_edit_opens_node_dialog', async () => {
    const user = userEvent.setup()
    const onEdit = vi.fn()
    const node = makeNode({ id: 'node-7' })
    renderPanel({ node, onEdit })

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEdit).toHaveBeenCalledWith(node)
  })

  it('test_delete_fires_mutation', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseDeleteNode.mockReturnValue(mutationResult({ mutate }))
    renderPanel({ node: makeNode({ id: 'node-42' }) })

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(mutate).toHaveBeenCalledWith('node-42', expect.objectContaining({}))
  })

  it('test_undo_visible_only_when_applicable', () => {
    // Unmodified node (updated_at === created_at) — no Undo.
    const { unmount } = renderPanel({
      node: makeNode({ created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }),
    })
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
    unmount()

    // Modified node — Undo appears.
    renderPanel({
      node: makeNode({ created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' }),
    })
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })

  it('fires the undo mutation when Undo is clicked', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseUndoNode.mockReturnValue(
      mutationResult({ mutate }) as unknown as ReturnType<typeof useUndoNode>,
    )
    renderPanel({ node: makeNode({ id: 'node-9', updated_at: '2026-02-01T00:00:00Z' }) })

    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(mutate).toHaveBeenCalledWith('node-9')
  })
})
