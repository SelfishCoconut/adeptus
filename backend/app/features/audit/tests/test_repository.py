"""Repository tests for the audit log (Slice 10 task 4).

Run on in-memory SQLite. True concurrent no-fork locking is a Postgres property
(SQLite ignores FOR UPDATE) and is covered by the integration suite; here we verify
serialized correctness: contiguous seq, unbroken prev_hash linkage, paging, filters,
and the append-only invariant.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.audit import repository
from app.features.audit.hashing import GENESIS_HASH, compute_entry_hash
from app.features.audit.schemas import AuditContent

# pytest-asyncio is configured with asyncio_mode="auto" — no per-test mark needed.


async def _append(db: AsyncSession, **kw: object) -> object:
    entry = await repository.append_entry(db, **kw)  # type: ignore[arg-type]
    await db.commit()
    return entry


async def test_append_assigns_sequential_seq(db_session: AsyncSession) -> None:
    e1 = await _append(db_session, action="login", actor_user_id=uuid4())
    e2 = await _append(db_session, action="logout", actor_user_id=uuid4())
    e3 = await _append(db_session, action="login", actor_user_id=uuid4())
    assert [e1.seq, e2.seq, e3.seq] == [1, 2, 3]  # type: ignore[attr-defined]


async def test_genesis_uses_zero_prev_hash(db_session: AsyncSession) -> None:
    e1 = await _append(db_session, action="login", actor_user_id=uuid4())
    assert e1.prev_hash == GENESIS_HASH  # type: ignore[attr-defined]


async def test_append_links_prev_hash(db_session: AsyncSession) -> None:
    e1 = await _append(db_session, action="login", actor_user_id=uuid4())
    e2 = await _append(db_session, action="logout", actor_user_id=uuid4())
    assert e2.prev_hash == e1.entry_hash  # type: ignore[attr-defined]


async def test_append_entry_hash_matches_recompute(db_session: AsyncSession) -> None:
    # The stored entry_hash must equal compute_entry_hash over the stored content —
    # the writer/verifier agreement, asserted against a *persisted* row (Risk 2).
    actor = uuid4()
    eng = uuid4()
    e = await _append(
        db_session,
        action="graph_node_created",
        actor_user_id=actor,
        engagement_id=eng,
        target_type="node",
        target_id=str(uuid4()),
        payload={"label": "10.0.0.5"},
    )
    content = AuditContent(
        seq=e.seq,  # type: ignore[attr-defined]
        created_at=e.created_at,  # type: ignore[attr-defined]
        action=e.action,  # type: ignore[attr-defined]
        actor_user_id=e.actor_user_id,  # type: ignore[attr-defined]
        engagement_id=e.engagement_id,  # type: ignore[attr-defined]
        target_type=e.target_type,  # type: ignore[attr-defined]
        target_id=e.target_id,  # type: ignore[attr-defined]
        self_approved=e.self_approved,  # type: ignore[attr-defined]
        payload=e.payload,  # type: ignore[attr-defined]
    )
    assert e.entry_hash == compute_entry_hash(GENESIS_HASH, content)  # type: ignore[attr-defined]


async def test_concurrent_appends_serialize_no_fork(db_session: AsyncSession) -> None:
    # SQLite can't exercise real concurrency; assert the chain is contiguous and
    # unbroken across N appends (Postgres covers the true race in integration).
    n = 10
    entries = [await _append(db_session, action="login", actor_user_id=uuid4()) for _ in range(n)]
    seqs = [e.seq for e in entries]  # type: ignore[attr-defined]
    assert seqs == list(range(1, n + 1))
    hashes = [e.entry_hash for e in entries]  # type: ignore[attr-defined]
    assert len(set(hashes)) == n  # no two entries share a hash (no fork)
    for prev, cur in zip(entries, entries[1:], strict=False):
        assert cur.prev_hash == prev.entry_hash  # type: ignore[attr-defined]


async def test_list_for_engagement_newest_first_paginates(db_session: AsyncSession) -> None:
    eng = uuid4()
    for _ in range(5):
        await _append(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng)

    page1, cur1 = await repository.list_for_engagement(db_session, engagement_id=eng, limit=2)
    assert [e.seq for e in page1] == [5, 4]
    assert cur1 == 4

    page2, cur2 = await repository.list_for_engagement(
        db_session, engagement_id=eng, cursor_seq=cur1, limit=2
    )
    assert [e.seq for e in page2] == [3, 2]
    assert cur2 == 2

    page3, cur3 = await repository.list_for_engagement(
        db_session, engagement_id=eng, cursor_seq=cur2, limit=2
    )
    assert [e.seq for e in page3] == [1]
    assert cur3 is None


async def test_list_for_engagement_isolates_other_engagements(db_session: AsyncSession) -> None:
    eng_a, eng_b = uuid4(), uuid4()
    await _append(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng_a)
    await _append(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng_b)
    rows, _ = await repository.list_for_engagement(db_session, engagement_id=eng_a, limit=50)
    assert len(rows) == 1
    assert all(e.engagement_id == eng_a for e in rows)


async def test_list_for_engagement_self_approved_filter(db_session: AsyncSession) -> None:
    eng = uuid4()
    await _append(
        db_session,
        action="approval_granted",
        actor_user_id=uuid4(),
        engagement_id=eng,
        self_approved=True,
    )
    await _append(
        db_session,
        action="approval_granted",
        actor_user_id=uuid4(),
        engagement_id=eng,
        self_approved=False,
    )
    await _append(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng)

    only_true, _ = await repository.list_for_engagement(
        db_session, engagement_id=eng, self_approved=True, limit=50
    )
    assert [e.self_approved for e in only_true] == [True]

    only_false, _ = await repository.list_for_engagement(
        db_session, engagement_id=eng, self_approved=False, limit=50
    )
    assert [e.self_approved for e in only_false] == [False]


async def test_list_global_filters_by_action(db_session: AsyncSession) -> None:
    await _append(db_session, action="login", actor_user_id=uuid4())
    await _append(db_session, action="logout", actor_user_id=uuid4())
    await _append(db_session, action="login", actor_user_id=uuid4())
    # An engagement-scoped entry must NOT appear in the global list.
    await _append(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=uuid4())

    logins, _ = await repository.list_global(db_session, action="login", limit=50)
    assert [e.action for e in logins] == ["login", "login"]

    all_global, _ = await repository.list_global(db_session, limit=50)
    assert {e.action for e in all_global} == {"login", "logout"}


async def test_iter_chain_ordered_ascending(db_session: AsyncSession) -> None:
    for _ in range(4):
        await _append(db_session, action="login", actor_user_id=uuid4())
    seqs = [e.seq async for e in repository.iter_chain_ordered(db_session)]
    assert seqs == [1, 2, 3, 4]


async def test_append_self_seeds_missing_head(db_session: AsyncSession) -> None:
    from sqlalchemy import delete

    from app.features.audit.models import AuditChainHead

    await db_session.execute(delete(AuditChainHead))
    await db_session.commit()

    e = await _append(db_session, action="login", actor_user_id=uuid4())
    assert e.seq == 1  # type: ignore[attr-defined]
    assert e.prev_hash == GENESIS_HASH  # type: ignore[attr-defined]


def test_append_only_no_update_delete() -> None:
    public = {n for n in dir(repository) if not n.startswith("_")}
    forbidden = {n for n in public if any(k in n for k in ("update", "delete", "remove"))}
    assert forbidden == set(), f"audit repository must be append-only; found {forbidden}"
    assert "append_entry" in public
