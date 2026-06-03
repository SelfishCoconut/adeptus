import { describe, expect, it } from 'vitest'
import { toElements } from './toElements'
import type { GraphSnapshot, Node, Edge } from '../api'

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

function snapshot(nodes: Node[], edges: Edge[]): GraphSnapshot {
  return { nodes, edges }
}

describe('toElements', () => {
  it('test_maps_nodes_and_edges_to_elements', () => {
    const a = makeNode({ id: 'node-a', type: 'host', label: '10.0.0.1' })
    const b = makeNode({ id: 'node-b', type: 'service', label: 'nginx' })
    const edge = makeEdge({ id: 'edge-1', source_id: 'node-a', target_id: 'node-b' })

    const els = toElements(snapshot([a, b], [edge]))

    expect(els).toHaveLength(3)
    const nodeA = els.find((e) => e.data.id === 'node-a')
    expect(nodeA?.group).toBe('nodes')
    expect(nodeA?.data).toMatchObject({ id: 'node-a', label: '10.0.0.1', type: 'host' })

    const e = els.find((el) => el.data.id === 'edge-1')
    expect(e?.group).toBe('edges')
    expect(e?.data).toMatchObject({ source: 'node-a', target: 'node-b' })
  })

  it('test_edge_label_is_relation', () => {
    const a = makeNode({ id: 'node-a' })
    const b = makeNode({ id: 'node-b' })
    const edge = makeEdge({ source_id: 'node-a', target_id: 'node-b', relation: 'exploits' })

    const els = toElements(snapshot([a, b], [edge]))
    const e = els.find((el) => el.group === 'edges')

    expect(e?.data.label).toBe('exploits')
  })

  it('test_drops_edge_with_missing_endpoint', () => {
    const a = makeNode({ id: 'node-a' })
    // node-b is absent from the snapshot — the edge references a missing target.
    const danglingTarget = makeEdge({ source_id: 'node-a', target_id: 'node-b' })
    const danglingSource = makeEdge({
      id: 'edge-2',
      source_id: 'ghost',
      target_id: 'node-a',
    })

    const els = toElements(snapshot([a], [danglingTarget, danglingSource]))

    expect(els.filter((e) => e.group === 'edges')).toHaveLength(0)
    expect(els).toHaveLength(1) // only node-a
  })

  it('keeps edges whose endpoints are both present', () => {
    const a = makeNode({ id: 'node-a' })
    const b = makeNode({ id: 'node-b' })
    const good = makeEdge({ id: 'good', source_id: 'node-a', target_id: 'node-b' })
    const bad = makeEdge({ id: 'bad', source_id: 'node-a', target_id: 'gone' })

    const els = toElements(snapshot([a, b], [good, bad]))
    const edgeIds = els.filter((e) => e.group === 'edges').map((e) => e.data.id)

    expect(edgeIds).toEqual(['good'])
  })

  it('test_empty_snapshot_yields_no_elements', () => {
    expect(toElements(snapshot([], []))).toEqual([])
  })

  it('handles undefined / null snapshot defensively', () => {
    expect(toElements(undefined)).toEqual([])
    expect(toElements(null)).toEqual([])
  })
})
