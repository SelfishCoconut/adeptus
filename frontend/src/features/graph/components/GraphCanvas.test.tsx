import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type cytoscape from 'cytoscape'
import { GraphCanvas } from './GraphCanvas'
import { useGraph } from '../api'
import type { Node, Edge } from '../api'
import { usePinStore } from '../store/pinStore'
import { PINNED_CLASS } from '../cytoscape/styles'

// ---------------------------------------------------------------------------
// Mock react-cytoscapejs — the real canvas can't render under jsdom (Risk 2).
// The mock captures the props handed to <CytoscapeComponent> and simulates
// Cytoscape calling back with a core instance so we can drive tap events.
// ---------------------------------------------------------------------------

interface TapEvent {
  target: unknown
}

const h = vi.hoisted(() => ({
  lastElements: [] as cytoscape.ElementDefinition[],
  nodeTap: null as ((evt: TapEvent) => void) | null,
  bgTap: null as ((evt: TapEvent) => void) | null,
  layoutRun: vi.fn(),
  fit: vi.fn(),
  cyInstance: null as unknown,
}))

vi.mock('react-cytoscapejs', () => {
  const fakeCy = {
    removeListener: () => {},
    on: (event: string, a: unknown, b?: unknown) => {
      if (event === 'tap' && typeof a === 'function') {
        h.bgTap = a as (evt: TapEvent) => void
      } else if (event === 'tap' && a === 'node') {
        h.nodeTap = b as (evt: TapEvent) => void
      }
    },
    layout: () => ({ run: h.layoutRun }),
    fit: h.fit,
  }
  h.cyInstance = fakeCy
  return {
    default: (props: {
      elements: cytoscape.ElementDefinition[]
      cy?: (cy: unknown) => void
    }) => {
      h.lastElements = props.elements
      props.cy?.(fakeCy)
      return null
    },
  }
})

vi.mock('../api', () => ({
  useGraph: vi.fn(),
}))

const mockedUseGraph = vi.mocked(useGraph)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

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

function makeEdge(overrides: Partial<Edge> = {}): Edge {
  return {
    id: 'edge-1',
    engagement_id: ENGAGEMENT_ID,
    source_id: 'node-a',
    target_id: 'node-b',
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

function renderCanvas(onSelectNode: (node: Node | null) => void = vi.fn()) {
  return render(<GraphCanvas engagementId={ENGAGEMENT_ID} onSelectNode={onSelectNode} />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphCanvas', () => {
  beforeEach(() => {
    mockedUseGraph.mockReset()
    h.nodeTap = null
    h.bgTap = null
    h.layoutRun.mockReset()
    h.fit.mockReset()
    h.lastElements = []
    localStorage.clear()
    usePinStore.setState({ pinnedByEngagement: {} })
  })

  it('test_renders_loading_skeleton', () => {
    mockedUseGraph.mockReturnValue(graphResult({ isLoading: true }))
    renderCanvas()
    expect(screen.getByTestId('graph-canvas-skeleton')).toBeInTheDocument()
  })

  it('test_renders_error_state', () => {
    mockedUseGraph.mockReturnValue(
      graphResult({ isError: true, error: new Error('Boom') }),
    )
    renderCanvas()
    expect(screen.getByRole('alert')).toHaveTextContent('Boom')
  })

  it('test_renders_empty_state', () => {
    mockedUseGraph.mockReturnValue(graphResult({ data: { nodes: [], edges: [] } }))
    renderCanvas()
    expect(screen.getByTestId('graph-canvas-empty')).toBeInTheDocument()
    expect(screen.getByText('No graph entities yet — add one.')).toBeInTheDocument()
  })

  it('test_passes_computed_elements_to_cytoscape', () => {
    const a = makeNode({ id: 'node-a', label: '10.0.0.1', type: 'host' })
    const b = makeNode({ id: 'node-b', label: 'nginx', type: 'service' })
    const edge = makeEdge({ source_id: 'node-a', target_id: 'node-b' })
    mockedUseGraph.mockReturnValue(
      graphResult({ data: { nodes: [a, b], edges: [edge] } }),
    )

    renderCanvas()

    expect(h.lastElements).toHaveLength(3)
    const nodeEl = h.lastElements.find((e) => e.data.id === 'node-a')
    expect(nodeEl?.data).toMatchObject({ label: '10.0.0.1', type: 'host' })
    const edgeEl = h.lastElements.find((e) => e.data.id === 'edge-1')
    expect(edgeEl?.data).toMatchObject({ source: 'node-a', target: 'node-b', label: 'runs' })
  })

  it('tags pinned nodes with the pinned class', () => {
    const a = makeNode({ id: 'node-a' })
    mockedUseGraph.mockReturnValue(graphResult({ data: { nodes: [a], edges: [] } }))
    usePinStore.getState().togglePin(ENGAGEMENT_ID, 'node-a')

    renderCanvas()

    const nodeEl = h.lastElements.find((e) => e.data.id === 'node-a')
    expect(nodeEl?.classes).toBe(PINNED_CLASS)
  })

  it('test_selecting_node_invokes_onSelectNode', () => {
    const a = makeNode({ id: 'node-a', label: '10.0.0.1' })
    mockedUseGraph.mockReturnValue(graphResult({ data: { nodes: [a], edges: [] } }))
    const onSelectNode = vi.fn()

    renderCanvas(onSelectNode)

    expect(h.nodeTap).toBeTypeOf('function')
    h.nodeTap?.({ target: { id: () => 'node-a' } })
    expect(onSelectNode).toHaveBeenCalledWith(a)
  })

  it('deselects when the canvas background is tapped', () => {
    const a = makeNode({ id: 'node-a' })
    mockedUseGraph.mockReturnValue(graphResult({ data: { nodes: [a], edges: [] } }))
    const onSelectNode = vi.fn()

    renderCanvas(onSelectNode)

    expect(h.bgTap).toBeTypeOf('function')
    h.bgTap?.({ target: h.cyInstance })
    expect(onSelectNode).toHaveBeenCalledWith(null)
  })

  it('re-runs layout and fits on the Re-layout / Fit control', async () => {
    const user = userEvent.setup()
    const a = makeNode({ id: 'node-a' })
    mockedUseGraph.mockReturnValue(graphResult({ data: { nodes: [a], edges: [] } }))

    renderCanvas()

    await user.click(screen.getByRole('button', { name: 'Re-layout / Fit' }))
    expect(h.layoutRun).toHaveBeenCalled()
    expect(h.fit).toHaveBeenCalled()
  })
})
