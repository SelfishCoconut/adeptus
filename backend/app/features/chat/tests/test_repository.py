"""Repository tests against a real (SQLite in-memory) async session."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.chat import repository as repo
from app.features.chat.models import ChatMessage


async def _seed_turn(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    content: str,
) -> tuple[ChatMessage, ChatMessage]:
    user_msg, assistant_msg = await repo.insert_user_and_pending_assistant(
        db, engagement_id=engagement_id, user_id=user_id, content=content
    )
    await db.commit()
    return user_msg, assistant_msg


@pytest.mark.asyncio
async def test_insert_pending_pair(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    user_msg, assistant_msg = await _seed_turn(
        db_session, engagement_id=eng, user_id=user, content="hello"
    )

    assert user_msg.role == "user"
    assert user_msg.content == "hello"
    assert user_msg.status == "complete"

    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == ""
    assert assistant_msg.status == "pending"
    # The assistant placeholder sorts strictly after the user message.
    assert assistant_msg.created_at > user_msg.created_at


@pytest.mark.asyncio
async def test_get_message_for_owner_scopes_to_user(db_session: AsyncSession) -> None:
    eng, owner, other = uuid4(), uuid4(), uuid4()
    _, assistant_msg = await _seed_turn(db_session, engagement_id=eng, user_id=owner, content="hi")

    msg_id = cast(UUID, assistant_msg.id)
    found = await repo.get_message_for_owner(db_session, message_id=msg_id, user_id=owner)
    assert found is not None
    assert found.id == assistant_msg.id

    # Another user must never resolve this message.
    leaked = await repo.get_message_for_owner(db_session, message_id=msg_id, user_id=other)
    assert leaked is None

    # A missing message resolves to None.
    missing = await repo.get_message_for_owner(db_session, message_id=uuid4(), user_id=owner)
    assert missing is None


@pytest.mark.asyncio
async def test_recent_messages_oldest_first_and_bounded(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    for i in range(5):
        await _seed_turn(db_session, engagement_id=eng, user_id=user, content=f"msg-{i}")

    # 5 turns = 10 rows; bound to the last 4 rows, oldest-first.
    window = await repo.recent_messages(db_session, engagement_id=eng, user_id=user, limit=4)
    assert len(window) == 4
    created = [m.created_at for m in window]
    assert created == sorted(created)  # ascending
    # The last two rows are the most recent turn (user msg-4 then its assistant).
    assert window[-2].role == "user"
    assert window[-2].content == "msg-4"
    assert window[-1].role == "assistant"


@pytest.mark.asyncio
async def test_recent_messages_isolated_per_user(db_session: AsyncSession) -> None:
    eng, user_a, user_b = uuid4(), uuid4(), uuid4()
    await _seed_turn(db_session, engagement_id=eng, user_id=user_a, content="a-secret")
    await _seed_turn(db_session, engagement_id=eng, user_id=user_b, content="b-secret")

    window_a = await repo.recent_messages(db_session, engagement_id=eng, user_id=user_a, limit=10)
    contents = [m.content for m in window_a]
    assert "a-secret" in contents
    assert "b-secret" not in contents  # §5.4 per-user isolation


@pytest.mark.asyncio
async def test_list_conversation_paginates_oldest_first(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    for i in range(6):
        await _seed_turn(db_session, engagement_id=eng, user_id=user, content=f"m{i}")
    # 12 rows total.

    page1, cursor1 = await repo.list_conversation(
        db_session, engagement_id=eng, user_id=user, cursor=None, limit=5
    )
    assert len(page1) == 5
    assert [m.created_at for m in page1] == sorted(m.created_at for m in page1)
    assert cursor1 is not None  # older rows remain

    page2, cursor2 = await repo.list_conversation(
        db_session, engagement_id=eng, user_id=user, cursor=cursor1, limit=5
    )
    assert len(page2) == 5
    # page2 is strictly older than page1.
    assert max(m.created_at for m in page2) <= min(m.created_at for m in page1)
    assert cursor2 is not None

    page3, cursor3 = await repo.list_conversation(
        db_session, engagement_id=eng, user_id=user, cursor=cursor2, limit=5
    )
    assert len(page3) == 2  # 12 - 5 - 5
    assert cursor3 is None  # last (oldest) page

    # No row appears twice across pages.
    all_ids = [m.id for m in page1 + page2 + page3]
    assert len(all_ids) == len(set(all_ids)) == 12


@pytest.mark.asyncio
async def test_finalize_assistant_complete(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    _, assistant_msg = await _seed_turn(db_session, engagement_id=eng, user_id=user, content="q")

    updated = await repo.finalize_assistant(
        db_session,
        message_id=cast(UUID, assistant_msg.id),
        content="the full answer",
        status="complete",
        model="qwen3.5:9b",
        prompt_tokens=10,
        completion_tokens=20,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.status == "complete"
    assert updated.content == "the full answer"
    assert updated.model == "qwen3.5:9b"
    assert updated.prompt_tokens == 10
    assert updated.completion_tokens == 20


@pytest.mark.asyncio
async def test_finalize_assistant_failed(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    _, assistant_msg = await _seed_turn(db_session, engagement_id=eng, user_id=user, content="q")

    updated = await repo.finalize_assistant(
        db_session,
        message_id=cast(UUID, assistant_msg.id),
        content="",
        status="failed",
        model="qwen3.5:9b",
        prompt_tokens=None,
        completion_tokens=None,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.status == "failed"
    assert updated.content == ""


@pytest.mark.asyncio
async def test_finalize_assistant_is_no_op_on_already_terminal(db_session: AsyncSession) -> None:
    """A second finalize matches zero pending rows and returns None (Risk 6 race guard)."""
    eng, user = uuid4(), uuid4()
    _, assistant_msg = await _seed_turn(db_session, engagement_id=eng, user_id=user, content="q")
    msg_id = cast(UUID, assistant_msg.id)

    first = await repo.finalize_assistant(
        db_session,
        message_id=msg_id,
        content="winner",
        status="complete",
        model="qwen3.5:9b",
        prompt_tokens=1,
        completion_tokens=1,
    )
    assert first is not None and first.status == "complete"

    # A racing second finalization finds no pending row → None, and must not overwrite.
    second = await repo.finalize_assistant(
        db_session,
        message_id=msg_id,
        content="loser",
        status="failed",
        model="other",
        prompt_tokens=None,
        completion_tokens=None,
    )
    await db_session.commit()
    assert second is None

    row = await repo.get_message_for_owner(db_session, message_id=msg_id, user_id=user)
    assert row is not None
    assert row.status == "complete"
    assert row.content == "winner"


@pytest.mark.asyncio
async def test_finalize_assistant_missing_returns_none(db_session: AsyncSession) -> None:
    result = await repo.finalize_assistant(
        db_session,
        message_id=uuid4(),
        content="x",
        status="complete",
        model=None,
        prompt_tokens=None,
        completion_tokens=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_insert_stashes_graph_context_on_assistant_only(db_session: AsyncSession) -> None:
    """The POST-time §5.3 input stash lands on the assistant row; the user row stays NULL."""
    eng, user = uuid4(), uuid4()
    stash = {"inputs": {"pinned_node_ids": [str(uuid4())], "recent_node_ids": []}}
    user_msg, assistant_msg = await repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng, user_id=user, content="hi", graph_context=stash
    )
    await db_session.commit()

    assert assistant_msg.graph_context == stash
    assert user_msg.graph_context is None  # NULL for user rows (§8.2)


@pytest.mark.asyncio
async def test_finalize_assistant_persists_graph_context(db_session: AsyncSession) -> None:
    """finalize overwrites the stash with the canonical per-turn debug record (Decision 4)."""
    eng, user = uuid4(), uuid4()
    _, assistant_msg = await _seed_turn(db_session, engagement_id=eng, user_id=user, content="q")
    msg_id = cast(UUID, assistant_msg.id)

    record = {
        "nodes": [{"id": str(uuid4()), "type": "host", "label": "h", "reasons": ["pinned"]}],
        "edges": [],
        "context_block": "## Relevant graph subset\n- (host) h",
        "raw_prompt": "system...\nuser...",
    }
    updated = await repo.finalize_assistant(
        db_session,
        message_id=msg_id,
        content="the answer",
        status="complete",
        model="qwen3.5:9b",
        prompt_tokens=1,
        completion_tokens=1,
        graph_context=record,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.graph_context == record


@pytest.mark.asyncio
async def test_finalize_without_graph_context_preserves_stash(db_session: AsyncSession) -> None:
    """Omitting graph_context leaves a previously-stashed record untouched (no clobber)."""
    eng, user = uuid4(), uuid4()
    stash = {"inputs": {"pinned_node_ids": [str(uuid4())]}}
    _, assistant_msg = await repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng, user_id=user, content="q", graph_context=stash
    )
    await db_session.commit()
    msg_id = cast(UUID, assistant_msg.id)

    updated = await repo.finalize_assistant(
        db_session,
        message_id=msg_id,
        content="answer",
        status="complete",
        model="qwen3.5:9b",
        prompt_tokens=1,
        completion_tokens=1,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.graph_context == stash
