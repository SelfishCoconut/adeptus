"""Unit tests for the §5.3 relevant-subset algorithm (Slice 12).

Pure tests: no DB, no I/O. ``GraphNode``/``GraphEdge`` rows are built in memory (the
builder only reads ``id``/``type``/``label``/``properties`` and the edge endpoints), so
these exercise every union arm, the N/K caps, foreign-id rejection, edge selection, the
full verbatim render, and the no-redaction guarantee without a session.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.features.chat import subset_builder
from app.features.chat.schemas import GraphSubsetReason
from app.features.graph.models import GraphEdge, GraphNode


def _node(
    label: str,
    *,
    node_type: str = "host",
    properties: dict[str, Any] | None = None,
    node_id: UUID | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id or uuid4(),
        engagement_id=uuid4(),
        type=node_type,
        label=label,
        properties=properties or {},
    )


def _edge(source: GraphNode, target: GraphNode, *, relation: str = "hosts") -> GraphEdge:
    return GraphEdge(
        id=uuid4(),
        engagement_id=uuid4(),
        source_id=source.id,
        target_id=target.id,
        relation=relation,
        properties={},
    )


def _ids(row: GraphNode | GraphEdge) -> UUID:
    return UUID(str(row.id))


def _build(
    nodes: list[GraphNode],
    *,
    edges: list[GraphEdge] | None = None,
    message: str = "",
    pinned: list[UUID] | None = None,
    recent: list[UUID] | None = None,
    mentioned: list[UUID] | None = None,
    n_recent: int = 15,
    k_mentioned: int = 10,
) -> subset_builder.GraphSubset:
    return subset_builder.build(
        nodes=nodes,
        edges=edges or [],
        message_text=message,
        pinned_node_ids=pinned or [],
        recent_node_ids=recent or [],
        mentioned_node_ids=mentioned or [],
        n_recent=n_recent,
        k_mentioned=k_mentioned,
    )


def test_pinned_always_included() -> None:
    a, b = _node("alpha"), _node("beta")
    subset = _build([a, b], pinned=[_ids(a)])

    assert [n.id for n in subset.nodes] == [_ids(a)]
    assert subset.nodes[0].reasons == [GraphSubsetReason.PINNED]
    assert subset.nodes_injected == 1


def test_keyword_ilike_matches_label() -> None:
    login = _node("/login", node_type="endpoint")
    host = _node("10.0.0.5", node_type="host")
    # Uppercase in the message proves the match is case-insensitive (ILIKE semantics).
    subset = _build([login, host], message="what should I try against the LOGIN endpoint?")

    ids = {n.id for n in subset.nodes}
    assert _ids(login) in ids
    assert _ids(host) not in ids
    assert subset.nodes[0].reasons == [GraphSubsetReason.KEYWORD]


def test_recent_truncated_to_n() -> None:
    nodes = [_node(f"n{i}") for i in range(4)]
    # Most-recent-first; with N=2 only the first two earn the `recent` reason.
    subset = _build(nodes, recent=[_ids(n) for n in nodes], n_recent=2)

    recent_ids = {n.id for n in subset.nodes if GraphSubsetReason.RECENT in n.reasons}
    assert recent_ids == {_ids(nodes[0]), _ids(nodes[1])}
    assert subset.nodes_injected == 2


def test_mentioned_truncated_to_k() -> None:
    nodes = [_node(f"m{i}") for i in range(5)]
    subset = _build(nodes, mentioned=[_ids(n) for n in nodes], k_mentioned=3)

    mentioned_ids = {n.id for n in subset.nodes if GraphSubsetReason.MENTIONED in n.reasons}
    assert mentioned_ids == {_ids(nodes[0]), _ids(nodes[1]), _ids(nodes[2])}
    assert subset.nodes_injected == 3


def test_foreign_ids_ignored() -> None:
    a = _node("alpha")
    foreign = uuid4()
    subset = _build(
        [a],
        pinned=[foreign],
        recent=[foreign],
        mentioned=[foreign],
    )
    # No keyword, only foreign ids → nothing survives the live-graph intersection (§17.1).
    assert subset.is_empty
    assert subset.context_block == ""


def test_union_dedupes_with_multiple_reasons() -> None:
    a = _node("login-portal", node_type="endpoint")
    subset = _build(
        [a],
        message="check the login flow",
        pinned=[_ids(a)],
        recent=[_ids(a)],
    )
    assert subset.nodes_injected == 1
    node = subset.nodes[0]
    # Reasons deduped into one node, ordered by priority (pinned, recent, keyword).
    assert node.reasons == [
        GraphSubsetReason.PINNED,
        GraphSubsetReason.RECENT,
        GraphSubsetReason.KEYWORD,
    ]


def test_edges_only_among_selected_nodes() -> None:
    a, b, c = _node("aaa"), _node("bbb"), _node("ccc")
    e_ab = _edge(a, b, relation="connects")
    e_ac = _edge(a, c, relation="connects")
    subset = _build([a, b, c], edges=[e_ab, e_ac], pinned=[_ids(a), _ids(b)])

    assert subset.edges_injected == 1
    assert subset.edges[0].id == _ids(e_ab)
    assert subset.edges[0].source_id == _ids(a)
    assert subset.edges[0].target_id == _ids(b)


def test_full_subset_rendered_verbatim() -> None:
    pinned = _node("pinned-host", node_type="host")
    recent = _node("recent-svc", node_type="service")
    keyword = _node("sqli-finding", node_type="vulnerability")
    subset = _build(
        [pinned, recent, keyword],
        message="any sqli-finding here?",
        pinned=[_ids(pinned)],
        recent=[_ids(recent)],
    )
    # Every selected node is present — nothing dropped, nothing summarized (Decision 3).
    assert subset.nodes_injected == 3
    for node in (pinned, recent, keyword):
        assert node.label in subset.context_block
    # Pinned is weighted first in the rendered block.
    assert subset.context_block.index("pinned-host") < subset.context_block.index("recent-svc")


def test_empty_graph_yields_empty_subset() -> None:
    subset = _build([], pinned=[uuid4()], message="anything at all")
    assert subset.is_empty
    assert subset.nodes == []
    assert subset.edges == []
    assert subset.context_block == ""


def test_content_not_redacted() -> None:
    secret = "hunter2-SECRET-do-not-strip"
    creds = _node(
        "db-root",
        node_type="credential",
        properties={"username": "root", "password": secret},
    )
    subset = _build([creds], pinned=[_ids(creds)])
    # Secret-looking property value reaches the (local) model verbatim (§5.5 / Risk 6).
    assert secret in subset.context_block
    assert "root" in subset.context_block
