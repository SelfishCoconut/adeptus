# Slice 08: Graph visualization (Cytoscape)

**Branch**: `slice-08-graph-visualization-cytoscape`
**GitHub Issue**: #24
**Status**: planned
**Risky**: no

---

## Goal

Render the engagement's shared graph as an interactive force-directed Cytoscape view in the workspace right pane, where nodes are draggable and selectable, and let a user pin nodes (pinned state is exposed for later AI @-mention consumption).

## User-visible demo

After this slice is merged, in the workspace right pane (the "Graph" region):

- Log in, open an engagement, and the Graph pane shows a **force-directed graph** of the engagement's live nodes and edges (served by the existing `useGraph` hook from Slice 07) instead of (or alongside) the stand-in node list.
- Nodes are **styled by `type`** (distinct colour/shape per `host`, `port`, `service`, `url`, `endpoint`, `vulnerability`, `credential`, `note`, `attack_path`) with the node `label` as the visible caption; **edges are labelled by their `relation`**.
- The user can **drag a node** to reposition it; the layout settles around it.
- Clicking a node **selects** it and surfaces the existing create/edit/delete/undo affordances from Slice 07 (the `NodeEditDialog` and delete/undo actions are reused, not duplicated) — e.g. an "Edit" and "Delete" action on the selected node.
- The user can **pin** a node (a pin toggle on the selected node / on the node itself); pinned nodes render with a visible "pinned" affordance (e.g. a pin badge / accent ring) and persist as pinned for that user within the engagement across reloads.
- An empty engagement shows a friendly empty state ("No graph entities yet — add one.") with the existing "Add node" affordance still reachable.
- A layout/zoom control lets the user re-run the layout and fit the graph to the viewport.

## Out of scope

- Does **NOT** consume pinned nodes into the AI "relevant subset" prompt injection — that is Slice 12 (§5.3, "all pinned nodes for the engagement (always included)"). This slice only establishes and **exposes** the pin state; wiring it into chat/AI context is later. (§5.4 pinning → "implicit @-mentions" consumer is a later AI slice.)
- Does **NOT** add WebSocket live-push of graph changes to other connected members — reads stay **pull-based** via `useGraph` (Slice 07 deferred live push; collaboration/presence is Slice 31). Refresh / TanStack-Query invalidation reflects local mutations; remote members' changes appear on the next refetch, not in real time.
- Does **NOT** implement attack-path-specific construction (drag-link to build attack paths) or attack-path rendering semantics — that is Slice 22 (§9.3, §8.3 "manual attack paths"). `attack_path` is rendered only as a generic node `type`.
- Does **NOT** add node annotations/notes UI or screenshot attachment (§8.3 free-form notes / §11.4) beyond what Slice 07's `NodeEditDialog` already edits (label + `properties`).
- Does **NOT** add or change any backend graph write path, the single-writer, or the data model (see Contract / Data model changes — both are "no change"). Pin state lives client-side (see resolved open question 1).
- Does **NOT** implement edge creation by drawing on the canvas; edge create/delete/undo continue to use the existing Slice 07 mutation surface. (Canvas-draw edge authoring can be a later polish; this slice keeps edge authoring out of the canvas to stay scoped.)
- Does **NOT** add certainty-percentage rendering on graph items (§5.3 uncertainty signalling) — that is Slice 13.

## Requirements traceability

- **§8.3 — Visualization** (quoted): *"Force-directed graph (Cytoscape via `react-cytoscapejs`), interactive and draggable. Manual annotation: users can add free-form notes to any node and pin nodes as 'interesting.' Pinned nodes also act as implicit AI mentions. Manual attack paths: users can drag/drop or click-link nodes to construct attack paths."* This slice delivers the force-directed, interactive, draggable Cytoscape view and node **pinning** ("pin nodes as 'interesting'"). The library is **fixed by the requirement** (`react-cytoscapejs`). "Manual attack paths" (drag/drop link construction) is explicitly deferred to Slice 22 (§9.3). Free-form note annotation beyond the existing `properties` editor is deferred.
- **§5.4 — Personas, Sessions & Mentions** (quoted): *"Pinned graph nodes behave as implicit @-mentions: pinning a node tells the AI to weight it heavily in subsequent reasoning."* This slice establishes the pin state and exposes it; the AI-weighting consumer is Slice 12 (§5.3). The pin set is the same one §5.3 references as *"all pinned nodes for the engagement (always included)"* in the relevant-subset union.
- **§11.1 — Frontend tech** (quoted): *"`react-cytoscapejs` for the force-directed graph"* and *"`zustand` for client state"* — this slice adds `cytoscape` + `react-cytoscapejs` and (per resolved open question 1) the project's first Zustand store for ephemeral pin state.
- **§11.2 — Workspace Layout** (quoted): *"Right: live force-directed graph."* This slice makes the right pane actually live and force-directed (Slice 07 shipped a stand-in list in this region).
- **§16 — Tech Stack Summary**: confirms `react-cytoscapejs` as the graph library; this slice does not deviate.
- **§17.1 — Engagement isolation**: the view reads only the current engagement's graph via the existing membership-scoped `useGraph` (`GET .../graph`); no cross-engagement data. Pin state is keyed by `engagementId` (and, since it is client-side per resolved question 1, never leaks across engagements).

## Contract

**No backend API change.** This slice is frontend-only and reuses the Slice 07 endpoints unchanged (`GET /api/v1/engagements/{engagement_id}/graph`, the node/edge CRUD + undo, and `GET .../graph/history`). Because there is no contract delta, `make generate-api` is **not** required for this slice (no task regenerates `frontend/openapi.json` / `shared/api/schema.ts`).

Frontend TypeScript types added (feature-local, in `src/features/graph/`):

```ts
// src/features/graph/components/GraphCanvas.tsx (props)
export interface GraphCanvasProps {
  engagementId: string
  /** Called when the user selects a node on the canvas (null = deselect). */
  onSelectNode: (node: Node | null) => void
}

// src/features/graph/store/pinStore.ts — ephemeral client state (Zustand)
export interface PinState {
  /** Map of engagementId -> set of pinned node ids (serialized as string[] for persistence). */
  pinnedByEngagement: Record<string, string[]>
  isPinned: (engagementId: string, nodeId: string) => boolean
  togglePin: (engagementId: string, nodeId: string) => void
  pinnedNodeIds: (engagementId: string) => string[]
  /** Drop pins for nodes that no longer exist in the live graph (called after each graph load). */
  reconcile: (engagementId: string, liveNodeIds: string[]) => void
}

// src/features/graph/cytoscape/styles.ts
// Mapping from NodeType (components['schemas']['NodeType']) to a Cytoscape style descriptor
// (colour + shape); edge style renders `relation` as the edge label.
export type NodeTypeStyle = { color: string; shape: string }
export const nodeTypeStyles: Record<Node['type'], NodeTypeStyle>
```

The `Node` / `Edge` / `GraphSnapshot` / `NodeType` types continue to come from the generated `@/shared/api` re-exports already present in `src/features/graph/api.ts` — they are **not** hand-written.

## Data model changes

**None.** No Alembic migration. No new tables, columns, FKs, or indexes.

Rationale (resolved open question 1): pin state is **ephemeral, client-side, per-user** Zustand state persisted to `localStorage`, not a graph-feature column. Persisting pins server-side would mean adding a write path (and therefore touching the single-writer and/or a new table), which contradicts the "keep backend changes minimal" guidance and §8.2 "no provenance attribution" sensibilities (a pin is a per-user UI weighting, not shared graph truth). §5.4 frames a pin as "tells **the AI** to weight it heavily in **subsequent reasoning**" — the AI prompt assembly (Slice 12, §5.3) reads the *current* pin set at turn time; it does not require durable cross-session server storage in this slice. If a future slice needs pins shared across team members or durable server-side, that is an additive change owned by the AI-context slice with its own ADR. See resolved open question 1 for the full trade-off.

## Tasks

Numbered continuously across the whole slice. Every commit subject cites its task id, e.g. `feat(slice-08): add cytoscape canvas (task 3)`. This slice has **no backend tasks**.

### Frontend tasks

1. **[S]** Add `cytoscape`, `react-cytoscapejs`, and their `@types/*` (if not bundled) to `frontend/package.json` and lockfile; pick a force-directed layout extension (`cytoscape-cose-bilkent` or the built-in `cose` layout — prefer the built-in `cose` to avoid an extra dependency unless settling quality is poor). **Verify React 19 compatibility** of `react-cytoscapejs` (see Risk 1) and record the resolution (peer-dep override, or a thin local wrapper around the `cytoscape` core if the React wrapper is incompatible). Document the layout/library decision inline.

2. **[S]** Add `src/features/graph/cytoscape/styles.ts` — `nodeTypeStyles` mapping each `NodeType` to a colour + shape, the Cytoscape `stylesheet` array (node label = `label`, edge label = `relation`, selected-node style, pinned-node style), and a shared layout config. Pure module, unit-tested for exhaustive `NodeType` coverage.

3. **[M]** Add `src/features/graph/cytoscape/toElements.ts` + test — pure function mapping a `GraphSnapshot` (`Node[]` + `Edge[]`) to Cytoscape `ElementDefinition[]` (nodes with `data.id/label/type`, edges with `data.id/source/target/label=relation`), filtering any edge whose endpoint is missing from the node set (defensive against eventual-consistency gaps). Tested in isolation.

4. **[M]** Add `src/features/graph/store/pinStore.ts` + test — Zustand store implementing `PinState` above, persisted to `localStorage` via `zustand/middleware` `persist`, keyed per engagement, with `reconcile` to drop pins for vanished nodes. This is the project's first Zustand store; colocate a `pinStore.test.ts`. (Per CLAUDE.md: Zustand for ephemeral client state.)

5. **[L]** Add `src/features/graph/components/GraphCanvas.tsx` + test — wraps `react-cytoscapejs` (`CytoscapeComponent`), consumes `useGraph(engagementId)`, renders elements via `toElements` with the `styles.ts` stylesheet and force-directed layout. Handles: loading skeleton, error state, empty state ("No graph entities yet — add one."), node **drag** (Cytoscape default; no extra wiring beyond `userPanningEnabled`/`grabbable`), node **select** (`tap`/`select` → `onSelectNode`), and pinned-node visual styling driven by `pinStore`. Re-runs layout + fit on a "Re-layout / Fit" control. Tests mock `react-cytoscapejs` (the canvas does not render in jsdom) and assert: elements computed from the query data are passed to the component, empty/loading/error states render, and selecting a node invokes `onSelectNode`.

6. **[M]** Add `src/features/graph/components/SelectedNodePanel.tsx` + test — a small inspector shown when a node is selected: shows type/label/properties, a **Pin / Unpin** toggle (calls `pinStore.togglePin`), and **Edit** / **Delete** / **Undo** actions that reuse the Slice 07 surface — Edit opens the existing `NodeEditDialog` (in edit mode), Delete calls `useDeleteNode`, Undo (visible only when applicable) calls `useUndoNode`. No new mutation hooks; reuse `api.ts`. Test pin toggle, edit-opens-dialog, delete fires mutation.

7. **[M]** Update `src/features/graph/components/GraphPane.tsx` + test — replace the stand-in list-first layout with the canvas-first layout: render `GraphCanvas` as the primary surface, wire selection into `SelectedNodePanel`, keep the "Add node" affordance and the `NodeEditDialog` (create + edit) it already owns, and keep the "Show history" toggle → `GraphHistoryPanel` (Slice 07 reuse). Call `pinStore.reconcile` with live node ids after each successful graph load. Decide whether `GraphNodeList` stays as a secondary "list view" toggle or is removed — **prefer keeping it behind a "List / Graph" view toggle** so the Slice 07 keyboard-accessible affordances (and the existing E2E selectors) survive and accessibility doesn't regress (the canvas alone is not screen-reader friendly). Update `GraphPane.test.tsx`.

8. **[S]** Confirm the workspace wiring in `src/features/workspace/WorkspaceShell.tsx` still mounts `GraphPane` under the `aria-label="Graph"` region (no structural change expected); add a brief code comment that this region is now the live force-directed graph (§11.2). Update `WorkspaceShell.test.tsx` only if assertions changed.

9. **[M]** Update/extend `frontend/playwright/graph.spec.ts` (or add `graph-visualization.spec.ts`) — one E2E journey covering the new surface: open an engagement, add two nodes + an edge (via the retained add/edit surface), assert the **graph canvas region** is present with the nodes rendered, select a node, **pin** it (assert pinned affordance persists across a reload), and confirm Edit/Delete from the selected-node panel still works. Keep the existing `E2E_STACK=1` opt-in guard so unit CI stays green without a live stack.

10. **[S]** Verify coverage ≥ 60% on `src/features/graph/` (`make test-frontend`); add focused tests for any uncovered branch (notably the canvas state branches and the pin store). Run `make lint` (tsc strict + eslint) — no `any`; narrow Cytoscape event payloads with `unknown` + guards.

## Test plan

- **Unit — frontend** (Vitest + RTL; coverage ≥ 60% on `src/features/graph/`):
  - `cytoscape/styles.test.ts` — `test_every_node_type_has_a_style` (exhaustive over the `NodeType` enum), `test_selected_and_pinned_styles_present`.
  - `cytoscape/toElements.test.ts` — `test_maps_nodes_and_edges_to_elements`, `test_edge_label_is_relation`, `test_drops_edge_with_missing_endpoint`, `test_empty_snapshot_yields_no_elements`.
  - `store/pinStore.test.ts` — `test_toggle_pin_adds_and_removes`, `test_pins_are_scoped_per_engagement`, `test_pins_persist_via_localStorage`, `test_reconcile_drops_pins_for_vanished_nodes`.
  - `components/GraphCanvas.test.tsx` — `test_renders_loading_skeleton`, `test_renders_empty_state`, `test_renders_error_state`, `test_passes_computed_elements_to_cytoscape` (mocked `react-cytoscapejs`), `test_selecting_node_invokes_onSelectNode`.
  - `components/SelectedNodePanel.test.tsx` — `test_pin_toggle_updates_store`, `test_edit_opens_node_dialog`, `test_delete_fires_mutation`, `test_undo_visible_only_when_applicable`.
  - `components/GraphPane.test.tsx` (updated) — `test_renders_graph_canvas_by_default`, `test_list_graph_view_toggle`, `test_add_node_opens_dialog`, `test_history_toggle_shows_panel`, `test_selecting_node_shows_selected_panel`.
- **Integration**: none beyond the unit layer — there is no backend change; the existing Slice 07 integration tests cover the data layer.
- **E2E** (Playwright, `E2E_STACK=1` opt-in): one journey in `graph.spec.ts` (extended) — add nodes + edge, assert the force-directed canvas region renders them, select + pin a node, reload, assert the pin persists, edit/delete from the selected-node panel.

## Acceptance criteria

- `make test` passes (eslint + tsc --noEmit + vitest [+ playwright when `E2E_STACK=1`]); coverage gate ≥ 60% on `src/features/graph/` holds.
- `make dev` brings up the stack; manually:
  1. Log in; open an engagement with no graph — the Graph pane shows the empty state.
  2. Add a `host` node and a `service` node and link them with an edge (existing add surface) — both nodes appear in a **force-directed canvas**, styled by type, with the edge labelled by its relation.
  3. **Drag** a node — it repositions and the layout responds.
  4. **Click** a node — it selects; the selected-node panel shows Edit / Delete / Pin actions.
  5. **Pin** the node — a pinned affordance appears; **reload the page** — the node is still pinned.
  6. **Edit** the node's label from the selected-node panel (reusing `NodeEditDialog`) — the canvas updates after the mutation; **Delete** it — it disappears from the canvas; open History → Undo (Slice 07 surface) — it reappears.
  7. Toggle to the "List" view (if retained) — the Slice 07 list still works.
- `gh pr view` shows green CI.
- No backend change landed (the diff touches only `frontend/`); `frontend/openapi.json` is unchanged.

## Risks

- **Risk 1 — `react-cytoscapejs` vs React 19.** The project is on React 19 (`react@^19`), but `react-cytoscapejs` historically pins React ≤18 in its peer deps and is lightly maintained. Installing it may produce peer-dependency conflicts or runtime issues. Mitigation: §8.3/§11.1/§16 mandate `react-cytoscapejs`, so attempt it first; if the peer range blocks install, use a peer-dep override (npm `overrides`) — the wrapper is a thin shim over `cytoscape` core. If it is genuinely incompatible at runtime, fall back to a **minimal local wrapper** that mounts `cytoscape` core into a `ref`'d `<div>` (same library, no React wrapper) and note this as an ADR-worthy deviation. Flag to the human if a real incompatibility forces the fallback. (Open question 2.)
- **Risk 2 — Canvas untestable in jsdom.** Cytoscape requires a real canvas/DOM-measurement that jsdom does not provide, so `GraphCanvas` cannot render its actual graph under Vitest. Mitigation: mock `react-cytoscapejs` in component tests and keep the data-shaping logic (`toElements`, `styles`) in pure, fully-unit-tested modules; rely on the Playwright E2E for the real-canvas path. This is why tasks 2–3 isolate the pure logic.
- **Risk 3 — Pin reconciliation / stale pins.** Client-side pins keyed by node id can dangle if a node is deleted on another client. Mitigation: `pinStore.reconcile(engagementId, liveNodeIds)` runs after each graph load to prune pins for nodes no longer present; tested.
- **Risk 4 — Layout instability / performance.** Force-directed layout can jitter or be slow on larger graphs and re-run on every refetch, fighting user drag positions. Mitigation: run the layout once on load (and on explicit "Re-layout"), not on every TanStack-Query refetch; preserve user-dragged positions between refetches where feasible (diff elements rather than rebuild). Keep node counts modest for v1 (web-app engagements; §1).
- **Risk 5 — Accessibility regression.** Replacing the keyboard-accessible Slice 07 list with a canvas-only view would regress a11y and break existing E2E selectors. Mitigation: retain the list behind a "List / Graph" view toggle (task 7) so role/text selectors and screen-reader access survive; the E2E adds canvas assertions rather than removing list assertions.
- **Risk 6 — First Zustand store conventions.** This introduces the first Zustand store in the codebase; getting the `persist` middleware shape wrong could leak state across engagements. Mitigation: key all state by `engagementId`, unit-test cross-engagement isolation (`test_pins_are_scoped_per_engagement`), and keep the store strictly ephemeral/client (no server coupling).

## Open questions for the human

1. **Where pin state lives — RESOLVED (client-side, ephemeral, per-user; default chosen).** Pins live in a Zustand store persisted to `localStorage`, keyed per engagement and per browser/user. Rationale: §5.4 defines a pin as a signal that "tells **the AI** to weight it heavily in subsequent reasoning" — a per-user UI weighting consumed at AI-turn time (Slice 12, §5.3), not shared graph truth. Persisting server-side would require a new write path / table and would brush against §8.2 "no provenance attribution" and the single-writer surface, contradicting the "keep backend changes minimal, no data-model change" guidance. **If the human wants pins shared across team members** (so a teammate's pin affects everyone's AI context), that is a deliberate scope change: it would add a `graph_node_pins` table (or per-user pin rows) owned by the AI-context slice with its own ADR. Surfacing this as the one decision the human may want to override; default stands unless told otherwise.

2. **`react-cytoscapejs` on React 19 — fallback acceptable?** The requirements name `react-cytoscapejs` explicitly, but it is lightly maintained and may not declare React 19 support. The plan attempts the named library first (with an npm `overrides` peer-dep pin if needed) and only falls back to a thin local wrapper over `cytoscape` core (same underlying library) if there is a genuine runtime incompatibility. Confirm the fallback is acceptable as a documented deviation, or whether you'd prefer pinning React or choosing a different React-19-native Cytoscape wrapper. (Defaulting to: try named lib → overrides → thin core wrapper; record as ADR if the wrapper is used.)

## Security review required?

**No.** This slice is frontend-only: it adds a visualization over the already-reviewed Slice 07 read endpoint and reuses Slice 07's already-reviewed write mutations unchanged. It does **not** touch auth, MCP, the audit log, the single-writer process, RAG isolation, egress, secrets, or approvals. Pin state is ephemeral client-side state scoped per engagement (no cross-engagement leakage, no server persistence, no new data path). No new backend surface, no contract change, no migration. The single-writer invariant and engagement-isolation `404` posture remain entirely as reviewed in Slice 07.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-03T20:14:45Z — d008b3c chore(plan): flip slice 07 in-review → done (#23)
- 2026-06-03T20:15:38Z — d008b3c chore(plan): flip slice 07 in-review → done (#23)
