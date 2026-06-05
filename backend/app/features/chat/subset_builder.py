"""The §5.3 "relevant subset" algorithm (Slice 12).

On every AI turn the model is given an explicitly-scoped subset of the engagement graph
rather than the whole graph. This module owns that selection, as a pure, fully-unit-tested
function: given the engagement's already-loaded live graph (nodes + edges), the user's
message text, and the three client-supplied id lists, it returns a :class:`GraphSubset`.

The subset is the union of four arms (§5.3):

  * **pinned**    — every pinned node (always included, §5.4), weighted first.
  * **recent**    — the last ``n_recent`` nodes the client says were touched this turn.
  * **mentioned** — the last ``k_mentioned`` @-mentioned nodes (empty until Slice 31).
  * **keyword**   — live nodes whose label matches a token of the current message
                    (a cheap case-insensitive substring match — the in-memory equivalent
                    of SQL ``ILIKE '%token%'`` over ``graph_nodes.label``, run here over
                    the already-loaded live nodes so the turn needs no second query;
                    Decision 2).

Every client-supplied id is intersected with the live node set, so unknown or foreign ids
are silently dropped (§17.1) — the client can only ever contribute *ids*, never content.

**No token budget (planning Decision 3):** every selected node is rendered in full and
verbatim — no budget check, no summarization, no aggregation, no redaction (§5.5). The
recent/mentioned arms are bounded by ``n_recent``/``k_mentioned``; the pinned and keyword
arms are included whole. This is a deliberate divergence from the literal §5.3 "hard token
budget … overflow summarized" clause; if real prompts grow too large, a budget would be
reintroduced here (the single place the algorithm lives — see slice Risk 2).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.features.chat.schemas import GraphSubsetEdge, GraphSubsetNode, GraphSubsetReason
from app.features.graph.models import GraphEdge, GraphNode

# Render-time priority: pinned weighted first, then mentioned, recent, keyword. Used both
# to order the nodes in the context block and to order each node's reason list.
_REASON_PRIORITY: dict[GraphSubsetReason, int] = {
    GraphSubsetReason.PINNED: 0,
    GraphSubsetReason.MENTIONED: 1,
    GraphSubsetReason.RECENT: 2,
    GraphSubsetReason.KEYWORD: 3,
}

# Keyword tokenization: alphanumeric runs, lowercased, short tokens dropped so common
# noise words ("is", "a", "to") can't pull half the graph into the prompt.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 3


@dataclass
class GraphSubset:
    """The resolved per-turn graph subset (the §5.3 union, fully rendered).

    ``nodes`` and ``edges`` are the structured records the §14 debug panel renders (ids,
    types, labels, inclusion reasons). ``context_block`` is the verbatim text prepended to
    the system prompt for this turn — empty when the subset is empty (no graph block is
    added in that case, preserving exact Slice-11 behavior).
    """

    nodes: list[GraphSubsetNode]
    edges: list[GraphSubsetEdge]
    context_block: str

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    @property
    def nodes_injected(self) -> int:
        return len(self.nodes)

    @property
    def edges_injected(self) -> int:
        return len(self.edges)


def build(
    *,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    message_text: str,
    pinned_node_ids: Sequence[UUID],
    recent_node_ids: Sequence[UUID],
    mentioned_node_ids: Sequence[UUID],
    n_recent: int,
    k_mentioned: int,
) -> GraphSubset:
    """Assemble the §5.3 relevant subset from the live graph + the turn's inputs.

    Pure: no I/O. ``nodes``/``edges`` are the engagement's live graph (already loaded by
    ``graph.repository.load_live_graph``); all selection, capping, ordering and rendering
    happen in memory over them.
    """
    live_by_id: dict[UUID, GraphNode] = {_uuid(n.id): n for n in nodes}

    reasons_by_id: dict[UUID, set[GraphSubsetReason]] = defaultdict(set)

    # Arm 1 — pinned: always included (§5.4), no cap.
    for nid in pinned_node_ids:
        if nid in live_by_id:
            reasons_by_id[nid].add(GraphSubsetReason.PINNED)

    # Arm 2 — recent: distinct, most-recent-first, truncated to N, then intersected.
    for nid in _distinct_capped(recent_node_ids, n_recent):
        if nid in live_by_id:
            reasons_by_id[nid].add(GraphSubsetReason.RECENT)

    # Arm 3 — mentioned: distinct, truncated to K, then intersected.
    for nid in _distinct_capped(mentioned_node_ids, k_mentioned):
        if nid in live_by_id:
            reasons_by_id[nid].add(GraphSubsetReason.MENTIONED)

    # Arm 4 — keyword: cheap ILIKE-equivalent of the message tokens over node labels.
    tokens = _keyword_tokens(message_text)
    if tokens:
        for nid, node in live_by_id.items():
            label_lower = node.label.lower()
            if any(tok in label_lower for tok in tokens):
                reasons_by_id[nid].add(GraphSubsetReason.KEYWORD)

    if not reasons_by_id:
        return GraphSubset(nodes=[], edges=[], context_block="")

    # Order nodes by their highest-priority reason, then label, then id (deterministic).
    ordered_ids = sorted(
        reasons_by_id,
        key=lambda nid: (
            min(_REASON_PRIORITY[r] for r in reasons_by_id[nid]),
            live_by_id[nid].label.lower(),
            str(nid),
        ),
    )
    selected_nodes = [live_by_id[nid] for nid in ordered_ids]

    subset_nodes = [
        GraphSubsetNode(
            id=nid,
            type=live_by_id[nid].type,
            label=live_by_id[nid].label,
            reasons=sorted(reasons_by_id[nid], key=lambda r: _REASON_PRIORITY[r]),
        )
        for nid in ordered_ids
    ]

    # Edges: only those whose BOTH endpoints are in the selected set (no islands' dangling
    # half-edges, no foreign node disclosure).
    selected_id_set = set(ordered_ids)
    selected_edges = [
        e
        for e in edges
        if _uuid(e.source_id) in selected_id_set and _uuid(e.target_id) in selected_id_set
    ]
    label_by_id = {nid: live_by_id[nid].label for nid in ordered_ids}
    selected_edges.sort(
        key=lambda e: (
            label_by_id[_uuid(e.source_id)].lower(),
            label_by_id[_uuid(e.target_id)].lower(),
            e.relation,
            str(_uuid(e.id)),
        )
    )
    subset_edges = [
        GraphSubsetEdge(
            id=_uuid(e.id),
            source_id=_uuid(e.source_id),
            target_id=_uuid(e.target_id),
            relation=e.relation,
        )
        for e in selected_edges
    ]

    context_block = _render_context_block(selected_nodes, subset_edges, label_by_id)
    return GraphSubset(nodes=subset_nodes, edges=subset_edges, context_block=context_block)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid(value: Any) -> UUID:
    """Normalize a model id column (typed as the SQLAlchemy UUID descriptor) to ``uuid.UUID``.

    ``load_live_graph`` returns ``uuid.UUID`` values at runtime; this keeps both mypy configs
    happy without scattering ``cast`` at every comparison (mirrors the chat service pattern)."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _distinct_capped(ids: Sequence[UUID], cap: int) -> list[UUID]:
    """Distinct ids preserving first-seen (most-recent-first) order, truncated to ``cap``."""
    seen: list[UUID] = []
    for nid in ids:
        if nid not in seen:
            seen.append(nid)
        if len(seen) >= cap:
            break
    return seen


def _keyword_tokens(text: str) -> set[str]:
    """Extract cheap keyword tokens from the message (lowercased, short tokens dropped)."""
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN}


def _render_props(properties: dict[str, Any] | None) -> str:
    """Render a node's properties verbatim as ``{k=v, …}`` (no redaction, §5.5).

    Keys are sorted for a deterministic block; values pass through ``str`` untouched so a
    secret-looking value reaches the local model intact (Risk 6 — redaction is forbidden)."""
    if not properties:
        return ""
    parts = [f"{key}={properties[key]}" for key in sorted(properties)]
    return "{" + ", ".join(parts) + "}"


def _render_context_block(
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphSubsetEdge],
    label_by_id: dict[UUID, str],
) -> str:
    """Render the selected nodes/edges to the verbatim text prepended to the system prompt.

    Each node line is ``(type) label {props}`` (§5.3 "type + label + properties"); pinned
    nodes sort first and the lead-in tells the model to weight them heavily (§5.4)."""
    if not nodes:
        return ""
    lines = [
        "## Relevant graph subset",
        (
            "The following entities from this engagement's knowledge graph are relevant to "
            "the current turn. Treat them as authoritative context about the target; "
            "entities listed first are pinned and should be weighted heavily."
        ),
        "",
        "Nodes:",
    ]
    for node in nodes:
        props = _render_props(node.properties)
        line = f"- ({node.type}) {node.label}"
        if props:
            line += f" {props}"
        lines.append(line)
    if edges:
        lines.append("")
        lines.append("Edges:")
        for edge in edges:
            src = label_by_id[edge.source_id]
            tgt = label_by_id[edge.target_id]
            lines.append(f"- ({src}) -[{edge.relation}]-> ({tgt})")
    return "\n".join(lines)
