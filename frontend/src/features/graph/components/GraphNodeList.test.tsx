import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GraphNodeList } from './GraphNodeList'
import { useGraph, useDeleteNode } from '../api'
import type { Node, Edge } from '../api'

vi.mock('../api', () => ({
  useGraph: vi.fn(),
  useDeleteNode: vi.fn(),
}))

const mockedUseGraph = vi.mocked(useGraph)
const mockedUseDeleteNode = vi.mocked(useDeleteNode)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeNode(overrides: Partial<Node> = {}): Node {
  return {
    id: '00000000-0000-0000-0000-0000000000aa',
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

function makeEdge(overrides: Partial<Edge> = {}): Edge {
  return {
    id: '00000000-0000-0000-0000-0000000000ee',
    engagement_id: ENGAGEMENT_ID,
    source_id: '00000000-0000-0000-0000-0000000000aa',
    target_id: '00000000-0000-0000-0000-0000000000bb',
    relation: 'runs',
    properties: {},
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function graphResult(overrides: Partial<ReturnType<typeof useGraph>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useGraph>
}

function deleteNodeResult(overrides: Partial<ReturnType<typeof useDeleteNode>> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useDeleteNode>
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderList(
  props: {
    onEditNode?: (node: Node) => void
    onAddNode?: () => void
  } = {},
) {
  return render(
    <GraphNodeList
      engagementId={ENGAGEMENT_ID}
      onEditNode={props.onEditNode ?? vi.fn()}
      onAddNode={props.onAddNode ?? vi.fn()}
    />,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphNodeList', () => {
  beforeEach(() => {
    mockedUseGraph.mockReset()
    mockedUseDeleteNode.mockReset()
    mockedUseDeleteNode.mockReturnValue(deleteNodeResult())
  })

  describe('loading state', () => {
    it('renders a skeleton while the query is loading', () => {
      mockedUseGraph.mockReturnValue(graphResult({ isLoading: true }))

      renderList()

      expect(screen.getByTestId('graph-node-list-skeleton')).toBeInTheDocument()
    })
  })

  describe('error state', () => {
    it('shows an alert with the error message on query failure', () => {
      mockedUseGraph.mockReturnValue(
        graphResult({ isError: true, error: new Error('Network timeout') }),
      )

      renderList()

      expect(screen.getByRole('alert')).toHaveTextContent('Network timeout')
    })

    it('shows a fallback error message when error is not an Error instance', () => {
      mockedUseGraph.mockReturnValue(
        graphResult({ isError: true, error: 'unexpected' as unknown as Error }),
      )

      renderList()

      expect(screen.getByRole('alert')).toHaveTextContent('Failed to load graph.')
    })
  })

  describe('empty state', () => {
    it('renders the empty-state copy when there are no nodes', () => {
      mockedUseGraph.mockReturnValue(
        graphResult({ data: { nodes: [], edges: [] } }),
      )

      renderList()

      expect(
        screen.getByText('No graph entities yet — add one.'),
      ).toBeInTheDocument()
    })
  })

  describe('node list', () => {
    it('renders a row per node with a type badge and label', () => {
      mockedUseGraph.mockReturnValue(
        graphResult({
          data: {
            nodes: [
              makeNode({ id: 'a', type: 'host', label: '10.0.0.1' }),
              makeNode({ id: 'b', type: 'service', label: 'nginx', engagement_id: ENGAGEMENT_ID }),
            ],
            edges: [],
          },
        }),
      )

      renderList()

      expect(screen.getByText('10.0.0.1')).toBeInTheDocument()
      expect(screen.getByText('nginx')).toBeInTheDocument()
      // Type badges
      expect(screen.getByText('host')).toBeInTheDocument()
      expect(screen.getByText('service')).toBeInTheDocument()
    })

    it('calls onEditNode with the correct node when Edit is clicked', async () => {
      const user = userEvent.setup()
      const onEditNode = vi.fn()
      const node = makeNode({ id: 'node-1', label: 'my-host' })
      mockedUseGraph.mockReturnValue(
        graphResult({ data: { nodes: [node], edges: [] } }),
      )

      renderList({ onEditNode })

      await user.click(screen.getByRole('button', { name: 'Edit' }))
      expect(onEditNode).toHaveBeenCalledWith(node)
    })

    it('calls deleteNode.mutate with the node id when Delete is clicked', async () => {
      const user = userEvent.setup()
      const mutateFn = vi.fn()
      mockedUseDeleteNode.mockReturnValue(deleteNodeResult({ mutate: mutateFn }))
      const node = makeNode({ id: 'node-42', label: 'target' })
      mockedUseGraph.mockReturnValue(
        graphResult({ data: { nodes: [node], edges: [] } }),
      )

      renderList()

      await user.click(screen.getByRole('button', { name: 'Delete' }))
      expect(mutateFn).toHaveBeenCalledWith('node-42')
    })

    it('disables Delete buttons while a deletion is pending', () => {
      mockedUseDeleteNode.mockReturnValue(deleteNodeResult({ isPending: true }))
      mockedUseGraph.mockReturnValue(
        graphResult({
          data: { nodes: [makeNode()], edges: [] },
        }),
      )

      renderList()

      expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
    })

    it('calls onAddNode when the Add node button is clicked', async () => {
      const user = userEvent.setup()
      const onAddNode = vi.fn()
      mockedUseGraph.mockReturnValue(
        graphResult({ data: { nodes: [makeNode()], edges: [] } }),
      )

      renderList({ onAddNode })

      await user.click(screen.getByRole('button', { name: 'Add node' }))
      expect(onAddNode).toHaveBeenCalled()
    })

    it('shows the correct node and edge counts in the toolbar', () => {
      mockedUseGraph.mockReturnValue(
        graphResult({
          data: {
            nodes: [makeNode({ id: 'a' }), makeNode({ id: 'b' })],
            edges: [makeEdge()],
          },
        }),
      )

      renderList()

      // The toolbar paragraph text is split across JSX nodes; use a function matcher.
      const toolbar = screen.getByText((_content, el) =>
        el?.tagName === 'P' && /2 nodes/.test(el.textContent ?? ''),
      )
      expect(toolbar).toBeInTheDocument()
      expect(toolbar.textContent).toMatch(/1 edge[^s]|1 edge$/)

    })
  })
})
