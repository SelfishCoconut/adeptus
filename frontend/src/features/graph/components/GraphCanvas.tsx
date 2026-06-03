// GraphCanvas — the live force-directed Cytoscape view of the engagement graph
// (slice 08, §8.3 / §11.2).
//
// Reads the graph via the Slice 07 `useGraph` hook, shapes it with the pure
// `toElements` mapper, and renders it through `react-cytoscapejs` with the
// shared `graphStylesheet` + `graphLayout`. Nodes are draggable (Cytoscape
// default) and selectable (tap -> `onSelectNode`). Pinned nodes are styled
// declaratively by tagging their element with `PINNED_CLASS` so a pin change
// re-renders without imperative `cy` mutation.
//
// NOTE: Cytoscape needs a real canvas + DOM measurement that jsdom lacks
// (Risk 2), so component tests mock `react-cytoscapejs`; the real-canvas path
// is exercised by the Playwright E2E.
import { useCallback, useEffect, useMemo, useRef } from 'react'
import CytoscapeComponent from 'react-cytoscapejs'
import type cytoscape from 'cytoscape'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGraph } from '../api'
import type { Node } from '../api'
import { usePinStore } from '../store/pinStore'
import { graphLayout, graphStylesheet, PINNED_CLASS } from '../cytoscape/styles'
import { toElements } from '../cytoscape/toElements'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphCanvasProps {
  engagementId: string
  /** Called when the user selects a node on the canvas (null = deselect). */
  onSelectNode: (node: Node | null) => void
}

/** Shared canvas height — keeps the skeleton, empty state, and live canvas aligned. */
const CANVAS_HEIGHT = 'h-[420px]'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphCanvas({ engagementId, onSelectNode }: GraphCanvasProps) {
  const { data, isLoading, isError, error } = useGraph(engagementId)

  // Select the stable map reference (not a fresh array) to avoid re-render
  // loops from Zustand v5 selectors that allocate.
  const pinnedByEngagement = usePinStore((s) => s.pinnedByEngagement)
  const pinnedSet = useMemo(
    () => new Set(pinnedByEngagement[engagementId] ?? []),
    [pinnedByEngagement, engagementId],
  )

  // Keep the latest nodes in a ref so the (stable) tap handler can resolve a
  // tapped node id back to its Node without re-binding listeners each render.
  const nodesRef = useRef<Node[]>([])
  useEffect(() => {
    nodesRef.current = data?.nodes ?? []
  }, [data])

  // Elements, with the pinned class baked onto pinned nodes (declarative).
  const elements = useMemo<cytoscape.ElementDefinition[]>(() => {
    return toElements(data).map((el) =>
      el.group === 'nodes' && typeof el.data.id === 'string' && pinnedSet.has(el.data.id)
        ? { ...el, classes: PINNED_CLASS }
        : el,
    )
  }, [data, pinnedSet])

  const cyRef = useRef<cytoscape.Core | null>(null)

  const handleCy = useCallback(
    (cy: cytoscape.Core) => {
      cyRef.current = cy
      // Re-binding on every init would stack listeners; clear taps first.
      cy.removeListener('tap')
      cy.on('tap', 'node', (evt) => {
        const id = (evt.target as cytoscape.NodeSingular).id()
        const found = nodesRef.current.find((n) => n.id === id) ?? null
        onSelectNode(found)
      })
      cy.on('tap', (evt) => {
        // Tap on empty background (target is the core itself) deselects.
        if (evt.target === cy) onSelectNode(null)
      })
    },
    [onSelectNode],
  )

  const handleRelayout = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.layout(graphLayout).run()
    cy.fit(undefined, 30)
  }, [])

  // ---- Non-canvas states -------------------------------------------------

  if (isLoading) {
    return (
      <div data-testid="graph-canvas-skeleton" className="flex flex-col gap-2">
        <Skeleton className={`${CANVAS_HEIGHT} w-full`} />
      </div>
    )
  }

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load graph.'}
      </p>
    )
  }

  const nodeCount = data?.nodes?.length ?? 0

  if (nodeCount === 0) {
    return (
      <div
        data-testid="graph-canvas-empty"
        className={`flex ${CANVAS_HEIGHT} items-center justify-center rounded-md border border-dashed`}
      >
        <p className="text-sm text-muted-foreground">
          No graph entities yet — add one.
        </p>
      </div>
    )
  }

  // ---- Live canvas -------------------------------------------------------

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-end">
        <Button variant="outline" size="sm" onClick={handleRelayout}>
          Re-layout / Fit
        </Button>
      </div>
      <div
        data-testid="graph-canvas"
        className={`${CANVAS_HEIGHT} w-full overflow-hidden rounded-md border bg-card`}
      >
        <CytoscapeComponent
          elements={elements}
          stylesheet={graphStylesheet}
          layout={graphLayout}
          cy={handleCy}
          className="h-full w-full"
        />
      </div>
    </div>
  )
}
