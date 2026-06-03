// Cytoscape styling for the engagement graph.
//
// Pure, dependency-light module (no React, no DOM) so it can be unit-tested in
// isolation — the live canvas does not render under jsdom (see slice 08 Risk 2).
//
// `nodeTypeStyles` maps every NodeType to a colour + shape; `graphStylesheet`
// is the Cytoscape stylesheet (node label = `label`, edge label = `relation`,
// plus :selected and .pinned visual treatments); `graphLayout` is the shared
// force-directed (built-in `cose`) layout config (slice 08, task 1 decision —
// no extra layout dependency).
import type cytoscape from 'cytoscape'
import type { Node } from '../api'

// ---------------------------------------------------------------------------
// Per-type node style
// ---------------------------------------------------------------------------

/** A Cytoscape visual descriptor for a node type: fill colour + node shape. */
export type NodeTypeStyle = { color: string; shape: cytoscape.Css.NodeShape }

/**
 * One entry per `NodeType`. Typed as `Record<Node['type'], …>` so adding a new
 * node type to the API enum is a compile error here until a style is supplied
 * (exhaustiveness — see `test_every_node_type_has_a_style`).
 */
export const nodeTypeStyles: Record<Node['type'], NodeTypeStyle> = {
  host: { color: '#2563eb', shape: 'ellipse' }, // blue
  port: { color: '#0d9488', shape: 'round-rectangle' }, // teal
  service: { color: '#16a34a', shape: 'hexagon' }, // green
  url: { color: '#4f46e5', shape: 'round-rectangle' }, // indigo
  endpoint: { color: '#7c3aed', shape: 'tag' }, // violet
  vulnerability: { color: '#dc2626', shape: 'triangle' }, // red
  credential: { color: '#d97706', shape: 'diamond' }, // amber
  note: { color: '#64748b', shape: 'round-rectangle' }, // slate
  attack_path: { color: '#db2777', shape: 'star' }, // pink
}

// ---------------------------------------------------------------------------
// Pinned-node class — toggled on elements by the canvas from the pin store.
// ---------------------------------------------------------------------------

/** Cytoscape class applied to pinned nodes; drives the `.pinned` style block. */
export const PINNED_CLASS = 'pinned'

// ---------------------------------------------------------------------------
// Stylesheet
// ---------------------------------------------------------------------------

const perTypeNodeStyles: cytoscape.StylesheetJson = (
  Object.keys(nodeTypeStyles) as Node['type'][]
).map((type) => ({
  selector: `node[type = "${type}"]`,
  style: {
    'background-color': nodeTypeStyles[type].color,
    shape: nodeTypeStyles[type].shape,
  },
}))

/**
 * The full stylesheet handed to `<CytoscapeComponent stylesheet={…} />`.
 * Order matters: later blocks override earlier ones for matching elements, so
 * the base node block comes first, per-type colour/shape next, then the
 * interaction states (`:selected`, `.pinned`).
 */
export const graphStylesheet: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      'background-color': '#94a3b8',
      shape: 'ellipse',
      width: 30,
      height: 30,
      color: '#0f172a',
      'font-size': 10,
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
      'text-wrap': 'ellipsis',
      'text-max-width': '120px',
      'border-width': 0,
    },
  },
  ...perTypeNodeStyles,
  {
    selector: 'edge',
    style: {
      label: 'data(label)',
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      width: 1.5,
      'line-color': '#cbd5e1',
      'target-arrow-color': '#cbd5e1',
      color: '#475569',
      'font-size': 8,
      'text-rotation': 'autorotate',
      'text-background-color': '#ffffff',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  {
    // Selected node: dark accent ring.
    selector: 'node:selected',
    style: {
      'border-width': 3,
      'border-color': '#0f172a',
    },
  },
  {
    // Pinned node: amber "double" ring so the pin reads even when unselected.
    selector: `node.${PINNED_CLASS}`,
    style: {
      'border-width': 3,
      'border-color': '#f59e0b',
      'border-style': 'double',
    },
  },
]

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/**
 * Shared force-directed layout. Built-in `cose` (no extra dependency, task 1).
 * `animate: false` keeps re-layouts snappy and avoids fighting user drags;
 * the canvas re-runs this only on load and on the explicit "Re-layout" control
 * (slice 08 Risk 4), not on every refetch.
 */
export const graphLayout: cytoscape.CoseLayoutOptions = {
  name: 'cose',
  animate: false,
  fit: true,
  padding: 30,
  randomize: false,
  componentSpacing: 80,
  idealEdgeLength: 80,
}
