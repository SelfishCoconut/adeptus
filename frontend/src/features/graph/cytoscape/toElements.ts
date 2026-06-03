// Pure mapping from a GraphSnapshot (Slice 07 shape) to Cytoscape elements.
//
// No React, no DOM — fully unit-testable (the live canvas is untestable under
// jsdom; see slice 08 Risk 2). Kept separate from <GraphCanvas> so the data
// shaping has full coverage independent of the canvas.
import type cytoscape from 'cytoscape'
import type { GraphSnapshot, Node, Edge } from '../api'

// ---------------------------------------------------------------------------
// Element data payloads
// ---------------------------------------------------------------------------

/** `data` payload for a node element. `type` drives the per-type style block. */
export interface NodeElementData {
  id: string
  label: string
  type: Node['type']
}

/** `data` payload for an edge element. `label` is the edge `relation`. */
export interface EdgeElementData {
  id: string
  source: string
  target: string
  label: string
}

// ---------------------------------------------------------------------------
// Mapping
// ---------------------------------------------------------------------------

function toNodeElement(node: Node): cytoscape.ElementDefinition {
  return {
    group: 'nodes',
    data: { id: node.id, label: node.label, type: node.type } satisfies NodeElementData,
  }
}

function toEdgeElement(edge: Edge): cytoscape.ElementDefinition {
  return {
    group: 'edges',
    data: {
      id: edge.id,
      source: edge.source_id,
      target: edge.target_id,
      label: edge.relation,
    } satisfies EdgeElementData,
  }
}

/**
 * Map a graph snapshot to Cytoscape `ElementDefinition[]` (nodes first, then
 * edges). Defensive against eventual-consistency gaps: any edge whose source or
 * target node is absent from the snapshot is dropped, since Cytoscape throws
 * when an edge references a non-existent node (slice 08 Risk 3 / task 3).
 */
export function toElements(
  snapshot: GraphSnapshot | undefined | null,
): cytoscape.ElementDefinition[] {
  const nodes = snapshot?.nodes ?? []
  const edges = snapshot?.edges ?? []

  const nodeIds = new Set(nodes.map((n) => n.id))

  const nodeElements = nodes.map(toNodeElement)
  const edgeElements = edges
    .filter((e) => nodeIds.has(e.source_id) && nodeIds.has(e.target_id))
    .map(toEdgeElement)

  return [...nodeElements, ...edgeElements]
}
