import { describe, expect, it } from 'vitest'
import type cytoscape from 'cytoscape'
import {
  graphLayout,
  graphStylesheet,
  nodeTypeStyles,
  PINNED_CLASS,
} from './styles'
import type { Node } from '../api'

// A stylesheet block is StylesheetStyle ({selector, style}) | StylesheetCSS
// ({selector, css}); we only author the `style` form, so narrow to it.
function styleOf(
  block: cytoscape.StylesheetJsonBlock | undefined,
): Record<string, unknown> | undefined {
  if (block && 'style' in block) return block.style as Record<string, unknown>
  return undefined
}

// The canonical NodeType enum (mirrors components['schemas']['NodeType']).
// Kept explicit so a change to the API enum surfaces here as a test failure
// alongside the compile error in styles.ts.
const ALL_NODE_TYPES: Node['type'][] = [
  'host',
  'port',
  'service',
  'url',
  'endpoint',
  'vulnerability',
  'credential',
  'note',
  'attack_path',
]

describe('nodeTypeStyles', () => {
  it('test_every_node_type_has_a_style', () => {
    for (const type of ALL_NODE_TYPES) {
      const style = nodeTypeStyles[type]
      expect(style, `missing style for node type "${type}"`).toBeDefined()
      expect(style.color).toMatch(/^#[0-9a-f]{6}$/i)
      expect(typeof style.shape).toBe('string')
      expect(style.shape.length).toBeGreaterThan(0)
    }
  })

  it('has no styles for unknown node types beyond the enum', () => {
    expect(Object.keys(nodeTypeStyles).sort()).toEqual([...ALL_NODE_TYPES].sort())
  })

  it('assigns a distinct colour per node type', () => {
    const colors = ALL_NODE_TYPES.map((t) => nodeTypeStyles[t].color)
    expect(new Set(colors).size).toBe(colors.length)
  })
})

describe('graphStylesheet', () => {
  function selectors() {
    return graphStylesheet.map((block) => block.selector)
  }

  it('labels nodes by data(label) and edges by data(label)=relation', () => {
    const nodeBase = graphStylesheet.find((b) => b.selector === 'node')
    const edgeBase = graphStylesheet.find((b) => b.selector === 'edge')
    expect(styleOf(nodeBase)).toMatchObject({ label: 'data(label)' })
    // toElements maps Edge.relation -> data.label, so the edge label binding
    // renders the relation.
    expect(styleOf(edgeBase)).toMatchObject({ label: 'data(label)' })
  })

  it('test_selected_and_pinned_styles_present', () => {
    expect(selectors()).toContain('node:selected')
    expect(selectors()).toContain(`node.${PINNED_CLASS}`)

    const pinned = graphStylesheet.find(
      (b) => b.selector === `node.${PINNED_CLASS}`,
    )
    expect(styleOf(pinned)).toMatchObject({ 'border-color': '#f59e0b' })
  })

  it('has a per-type selector for every node type', () => {
    const sel = selectors()
    for (const type of ALL_NODE_TYPES) {
      expect(sel).toContain(`node[type = "${type}"]`)
    }
  })
})

describe('graphLayout', () => {
  it('is the force-directed cose layout, non-animated and fit-to-viewport', () => {
    expect(graphLayout.name).toBe('cose')
    expect(graphLayout.animate).toBe(false)
    expect(graphLayout.fit).toBe(true)
  })
})
