"""Service tests — Ollama client and audit.record are mocked (CLAUDE.md).

Membership / persist / list / WS-auth paths run against a real SQLite session; the
streaming paths run against the ``db_factory`` (the service opens its own session) with
``ollama_client.stream_chat`` replaced by a deterministic fake.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError, NotFoundError
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.chat import plan_parser, service
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo
from app.features.graph import repository as graph_repo

# A fake stream_chat: takes the prompt messages + optional usage holder, yields str tokens.
FakeStream = Callable[..., AsyncIterator[str]]

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, username: str) -> User:
    user = await auth_repo.create_user(
        db, username=username, password_hash=_hasher.hash("pw"), role="user"
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_engagement(db: AsyncSession, owner_id: UUID, *, archived: bool = False) -> UUID:
    engagement = await eng_repo.create_engagement(
        db, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
    )
    if archived:
        engagement.status = "archived"
    await db.commit()
    await db.refresh(engagement)
    return cast(UUID, engagement.id)


# ---------------------------------------------------------------------------
# Fake Ollama streams
# ---------------------------------------------------------------------------


def _fake_stream(
    tokens: list[str], *, prompt_tokens: int = 5, completion_tokens: int = 3
) -> FakeStream:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        for tok in tokens:
            yield tok
        if usage is not None:
            usage.prompt_tokens = prompt_tokens
            usage.completion_tokens = completion_tokens

    return _gen


def _fake_unreachable() -> FakeStream:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        if False:  # pragma: no cover — make this a generator that raises on first step
            yield ""
        raise service.LlmUnreachableError("boom")

    return _gen


def _capture_prompt(captured: list[Sequence[OllamaChatMessage]], tokens: list[str]) -> FakeStream:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        captured.append(messages)
        for tok in tokens:
            yield tok

    return _gen


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_persists_pending_pair(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id))

    result = await service.send_message(
        db_session, engagement_id=eng_id, requester=user, content="hello"
    )
    await db_session.commit()

    assert result.user_message.role == "user"
    assert result.user_message.content == "hello"
    assert result.user_message.status == "complete"
    assert result.assistant_message.role == "assistant"
    assert result.assistant_message.status == "pending"
    assert result.assistant_message.content == ""


@pytest.mark.asyncio
async def test_send_message_non_member_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    outsider = await _seed_user(db_session, "outsider")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))

    with pytest.raises(NotFoundError):
        await service.send_message(
            db_session, engagement_id=eng_id, requester=outsider, content="hi"
        )


@pytest.mark.asyncio
async def test_send_message_missing_engagement_404(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, "owner")
    with pytest.raises(NotFoundError):
        await service.send_message(db_session, engagement_id=uuid4(), requester=user, content="hi")


@pytest.mark.asyncio
async def test_send_message_archived_409(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), archived=True)

    with pytest.raises(ConflictError):
        await service.send_message(db_session, engagement_id=eng_id, requester=user, content="hi")


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_only_own_conversation(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    other = await _seed_user(db_session, "other")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    # Make `other` a member too so both can chat in the same engagement.
    await eng_repo.add_member(db_session, engagement_id=eng_id, user_id=cast(UUID, other.id))
    await db_session.commit()

    await service.send_message(
        db_session, engagement_id=eng_id, requester=owner, content="owner-secret"
    )
    await service.send_message(
        db_session, engagement_id=eng_id, requester=other, content="other-secret"
    )
    await db_session.commit()

    owner_page = await service.list_messages(
        db_session, engagement_id=eng_id, requester=owner, cursor=None, limit=50
    )
    contents = [m.content for m in owner_page.items]
    assert "owner-secret" in contents
    assert "other-secret" not in contents  # §5.4 per-user isolation


@pytest.mark.asyncio
async def test_list_messages_non_member_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    outsider = await _seed_user(db_session, "outsider")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))

    with pytest.raises(NotFoundError):
        await service.list_messages(
            db_session, engagement_id=eng_id, requester=outsider, cursor=None, limit=50
        )


# ---------------------------------------------------------------------------
# WS auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_auth_rejects_non_owner(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    other = await _seed_user(db_session, "other")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    await eng_repo.add_member(db_session, engagement_id=eng_id, user_id=cast(UUID, other.id))
    await db_session.commit()

    _, assistant = await chat_repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng_id, user_id=cast(UUID, owner.id), content="hi"
    )
    await db_session.commit()

    # `other`'s session must not authenticate against `owner`'s message.
    other_session = await auth_repo.create_session(
        db_session,
        session_id=str(uuid4()),
        user_id=cast(UUID, other.id),
        expires_at=_future(),
    )
    await db_session.commit()

    result = await service.authenticate_ws_chat_message(
        db_session, session_id=cast(str, other_session.id), message_id=cast(UUID, assistant.id)
    )
    assert result is None


@pytest.mark.asyncio
async def test_ws_auth_resolves_owner(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    _, assistant = await chat_repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng_id, user_id=cast(UUID, owner.id), content="hi"
    )
    sess = await auth_repo.create_session(
        db_session, session_id=str(uuid4()), user_id=cast(UUID, owner.id), expires_at=_future()
    )
    await db_session.commit()

    result = await service.authenticate_ws_chat_message(
        db_session, session_id=cast(str, sess.id), message_id=cast(UUID, assistant.id)
    )
    assert result is not None
    assert result.id == assistant.id


@pytest.mark.asyncio
async def test_ws_auth_rejects_missing_session(db_session: AsyncSession) -> None:
    assert (
        await service.authenticate_ws_chat_message(db_session, session_id=None, message_id=uuid4())
        is None
    )


# ---------------------------------------------------------------------------
# stream_assistant_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_relays_tokens_then_done(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["Hel", "lo"]))
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    types = [c.type for c in chunks]
    assert types == ["token", "token", "done"]
    assert [c.data for c in chunks if c.type == "token"] == ["Hel", "lo"]


@pytest.mark.asyncio
async def test_stream_persists_complete_and_emits_ai_call(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["Hel", "lo"]))
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.status == "complete"
    assert row.content == "Hello"
    assert row.prompt_tokens == 5
    assert row.completion_tokens == 3

    # Exactly one ai_call, attributed to the user, status=complete.
    mock_audit_record.assert_awaited_once()
    call = mock_audit_record.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["action"].value == "ai_call"
    assert kwargs["actor_user_id"] == cast(UUID, message.user_id)
    assert kwargs["payload"]["status"] == "complete"


@pytest.mark.asyncio
async def test_stream_unreachable_persists_failed_and_emits_error(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_unreachable())
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert chunks[-1].type == "error"
    assert chunks[-1].message == service.UNREACHABLE_MESSAGE

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.status == "failed"

    mock_audit_record.assert_awaited_once()
    call = mock_audit_record.await_args
    assert call is not None
    assert call.kwargs["payload"]["status"] == "failed"


@pytest.mark.asyncio
async def test_stream_replays_completed_message(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Should never call Ollama for an already-terminal message.
    def _boom(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        raise AssertionError("Ollama must not be called on replay")

    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom)

    message = await _seed_pending(db_factory)
    async with db_factory() as s:
        await chat_repo.finalize_assistant(
            s,
            message_id=cast(UUID, message.id),
            content="stored answer",
            status="complete",
            model="qwen3.5:9b",
            prompt_tokens=1,
            completion_tokens=1,
        )
        await s.commit()

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert [c.type for c in chunks] == ["token", "done"]
    assert chunks[0].data == "stored answer"
    # No second ai_call on replay (Risk 6).
    mock_audit_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_uses_recent_window_verbatim(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))

    sensitive = "the password for box-7 is <kept-verbatim>"
    message = await _seed_pending(db_factory, content=sensitive)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    assert len(captured) == 1
    prompt = captured[0]
    # System prompt first, then the user message verbatim (§5.5 — no redaction).
    assert prompt[0].role == "system"
    user_contents = [m.content for m in prompt if m.role == "user"]
    assert sensitive in user_contents
    # The empty pending assistant placeholder is never sent.
    assert all(m.content for m in prompt)


# ---------------------------------------------------------------------------
# local helpers needing fixtures
# ---------------------------------------------------------------------------


def _future() -> datetime.datetime:
    return datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)


async def _seed_pending(
    factory: async_sessionmaker[AsyncSession], *, content: str = "what is sqli?"
) -> ChatMessage:
    """Seed a user + pending-assistant turn and return the (detached) assistant row."""
    async with factory() as s:
        owner = await _seed_user(s, f"owner-{uuid4().hex[:8]}")
        eng_id = await _seed_engagement(s, cast(UUID, owner.id))
        _, assistant = await chat_repo.insert_user_and_pending_assistant(
            s, engagement_id=eng_id, user_id=cast(UUID, owner.id), content=content
        )
        await s.commit()
        await s.refresh(assistant)
        return assistant


async def _seed_turn_with_graph(
    factory: async_sessionmaker[AsyncSession],
    *,
    content: str = "what is sqli?",
    node_specs: Sequence[tuple[str, str, dict[str, object]]] = (),
    pin_labels: Sequence[str] = (),
) -> ChatMessage:
    """Seed an engagement with a graph + a pending turn whose stash pins ``pin_labels``.

    Returns the (detached) pending assistant row ready for ``stream_assistant_reply``. Goes
    through ``service.send_message`` so the §5.3 inputs are stashed exactly as in production.
    """
    async with factory() as s:
        owner = await _seed_user(s, f"owner-{uuid4().hex[:8]}")
        eng_id = await _seed_engagement(s, cast(UUID, owner.id))
        label_to_id: dict[str, UUID] = {}
        for node_type, label, properties in node_specs:
            node = await graph_repo.insert_node(
                s, engagement_id=eng_id, node_type=node_type, label=label, properties=properties
            )
            label_to_id[label] = cast(UUID, node.id)
        await s.commit()

        result = await service.send_message(
            s,
            engagement_id=eng_id,
            requester=owner,
            content=content,
            pinned_node_ids=[label_to_id[label] for label in pin_labels],
        )
        await s.commit()
        assistant = await chat_repo.get_message_for_owner(
            s, message_id=result.assistant_message.id, user_id=cast(UUID, owner.id)
        )
        assert assistant is not None
        return assistant


# ---------------------------------------------------------------------------
# stream_assistant_reply — §5.3 relevant-subset injection (Slice 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_injects_pinned_node_into_prompt(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message = await _seed_turn_with_graph(
        db_factory,
        content="anything",
        node_specs=[("host", "target-host-xyz", {})],
        pin_labels=["target-host-xyz"],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    system_msg = captured[0][0]
    assert system_msg.role == "system"
    assert "target-host-xyz" in system_msg.content
    assert "## Relevant graph subset" in system_msg.content


@pytest.mark.asyncio
async def test_stream_keyword_match_included(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    # No pin — the node is pulled in purely by the keyword arm matching its label.
    message = await _seed_turn_with_graph(
        db_factory,
        content="what should I try against the login page?",
        node_specs=[("endpoint", "/login", {})],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    assert "/login" in captured[0][0].content


@pytest.mark.asyncio
async def test_stream_empty_graph_no_context_block(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    # Engagement with no graph at all — Slice-11 prompt must be preserved exactly.
    message = await _seed_turn_with_graph(db_factory, content="hi there")

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    system_msg = captured[0][0]
    # No graph block on an empty graph; the base prompt + the Slice-13 structured-output
    # instruction is all the system message carries.
    assert system_msg.content == service.SYSTEM_PROMPT + service.PLAN_CERTAINTY_INSTRUCTION
    assert "Relevant graph subset" not in system_msg.content


@pytest.mark.asyncio
async def test_stream_persists_graph_context_debug_record(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["done"]))
    message = await _seed_turn_with_graph(
        db_factory,
        content="anything",
        node_specs=[("host", "db-host", {})],
        pin_labels=["db-host"],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    gc = row.graph_context
    assert gc is not None
    assert [n["label"] for n in gc["nodes"]] == ["db-host"]
    assert "pinned" in gc["nodes"][0]["reasons"]
    assert "## Relevant graph subset" in gc["context_block"]
    assert gc["raw_prompt"]  # the rendered raw prompt is captured for §14


@pytest.mark.asyncio
async def test_ai_call_payload_has_subset_counts(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["ok"]))
    message = await _seed_turn_with_graph(
        db_factory,
        content="anything",
        node_specs=[("host", "only-host", {})],
        pin_labels=["only-host"],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    mock_audit_record.assert_awaited_once()
    call = mock_audit_record.await_args
    assert call is not None
    payload = call.kwargs["payload"]
    assert payload["graph_nodes_injected"] == 1
    assert payload["graph_edges_injected"] == 0


@pytest.mark.asyncio
async def test_prompt_graph_block_not_redacted(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    secret = "hunter2-SECRET-do-not-strip"
    message = await _seed_turn_with_graph(
        db_factory,
        content="anything",
        node_specs=[("credential", "db-root", {"password": secret})],
        pin_labels=["db-root"],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    # The secret-looking property value reaches the (local) model verbatim (§5.5 / Risk 6).
    assert secret in captured[0][0].content


# ---------------------------------------------------------------------------
# get_turn_debug — §14 debug-panel data source (Slice 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_turn_debug_owner_only_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    other = await _seed_user(db_session, "other")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    await eng_repo.add_member(db_session, engagement_id=eng_id, user_id=cast(UUID, other.id))
    await db_session.commit()

    result = await service.send_message(
        db_session, engagement_id=eng_id, requester=owner, content="private"
    )
    await db_session.commit()
    assistant_id = result.assistant_message.id

    # The owner can read their own turn's debug record.
    debug = await service.get_turn_debug(
        db_session, engagement_id=eng_id, requester=owner, message_id=assistant_id
    )
    assert debug.message_id == assistant_id

    # Another member of the same engagement cannot (per-user, §5.4 / Risk 5).
    with pytest.raises(NotFoundError):
        await service.get_turn_debug(
            db_session, engagement_id=eng_id, requester=other, message_id=assistant_id
        )


@pytest.mark.asyncio
async def test_get_turn_debug_wrong_engagement_404(db_session: AsyncSession) -> None:
    """A user cannot read their own debug record through a different engagement's path (Risk 5)."""
    owner = await _seed_user(db_session, "owner-xeng")
    eng_a = await _seed_engagement(db_session, cast(UUID, owner.id))
    eng_b = await _seed_engagement(db_session, cast(UUID, owner.id))

    # Owner sends a message in engagement B and gets an assistant turn there.
    result = await service.send_message(
        db_session, engagement_id=eng_b, requester=owner, content="hello from B"
    )
    await db_session.commit()
    assistant_id_b = result.assistant_message.id

    # Reading via engagement B (correct path) succeeds.
    debug = await service.get_turn_debug(
        db_session, engagement_id=eng_b, requester=owner, message_id=assistant_id_b
    )
    assert debug.message_id == assistant_id_b

    # Reading that same message via engagement A's path must 404 (cross-engagement leak guard).
    with pytest.raises(NotFoundError):
        await service.get_turn_debug(
            db_session, engagement_id=eng_a, requester=owner, message_id=assistant_id_b
        )


@pytest.mark.asyncio
async def test_get_turn_debug_non_assistant_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    result = await service.send_message(
        db_session, engagement_id=eng_id, requester=owner, content="hi"
    )
    await db_session.commit()

    # The USER row is owned by the caller but is not an assistant turn → 404.
    with pytest.raises(NotFoundError):
        await service.get_turn_debug(
            db_session,
            engagement_id=eng_id,
            requester=owner,
            message_id=result.user_message.id,
        )


# ---------------------------------------------------------------------------
# stream_assistant_reply — §5.3 visible plan + certainty signaling (Slice 13)
# ---------------------------------------------------------------------------


def _meta(
    *, plan: list[dict[str, object]] | None = None, claims: list[dict[str, object]] | None = None
) -> str:
    """Render a well-formed trailing <adeptus-meta> block (the model's structured output)."""
    payload = {"plan": plan or [], "claims": claims or []}
    return f"{plan_parser.START_MARKER}\n{json.dumps(payload)}\n{plan_parser.END_MARKER}"


def _chunk(text: str, size: int = 4) -> list[str]:
    """Split a reply into small fixed-size token slices to exercise the streamer's
    across-token-boundary marker buffering."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _tokens(prose: str, block: str) -> list[str]:
    return _chunk(prose + "\n\n" + block)


@pytest.mark.asyncio
async def test_prompt_appends_structured_instruction(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    system_content = captured[0][0].content
    assert service.PLAN_CERTAINTY_INSTRUCTION in system_content
    assert plan_parser.START_MARKER in system_content


@pytest.mark.asyncio
async def test_stream_strips_meta_block_from_tokens(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[{"step": "Enumerate", "status": "done"}],
        claims=[{"text": "likely apache", "certainty": 55}],
    )
    prose = "This is the visible answer to the operator."
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(_tokens(prose, block)))
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    token_data = "".join(c.data or "" for c in chunks if c.type == "token")
    assert "adeptus-meta" not in token_data  # the raw block never streamed (Risk 2)
    assert prose in token_data
    assert chunks[-1].type == "done"


@pytest.mark.asyncio
async def test_done_frame_carries_plan_and_claims(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[
            {"step": "Enumerate login", "status": "done"},
            {"step": "Try SQLi", "status": "in_progress"},
        ],
        claims=[{"text": "service is likely Apache", "certainty": 60}],
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(_tokens("Answer.", block))
    )
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.type == "done"
    assert done.plan is not None and len(done.plan) == 2
    assert done.plan[1].status.value == "in_progress"
    assert done.claims is not None and done.claims[0].certainty == 60


@pytest.mark.asyncio
async def test_stream_persists_plan_and_claims(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[{"step": "Check cookie flags", "status": "todo"}],
        claims=[{"text": "no HttpOnly", "certainty": 80}],
    )
    prose = "Clean prose only."
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(_tokens(prose, block)))
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.content == prose  # stored prose is block-stripped
    gc = row.graph_context
    assert gc is not None
    assert [p["step"] for p in gc["plan"]] == ["Check cookie flags"]
    assert gc["claims"][0]["certainty"] == 80
    # The UNSTRIPPED reply (incl. the block) is kept for the §14 debug panel.
    assert "adeptus-meta" in gc["model_output"]
    # Slice-12 keys survive on the same blob (Risk 6 — merge, not overwrite).
    assert "context_block" in gc and "raw_prompt" in gc


@pytest.mark.asyncio
async def test_claim_node_id_validated_against_graph(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db_factory() as s:
        owner = await _seed_user(s, f"owner-{uuid4().hex[:8]}")
        eng_id = await _seed_engagement(s, cast(UUID, owner.id))
        node = await graph_repo.insert_node(
            s, engagement_id=eng_id, node_type="service", label="apache", properties={}
        )
        await s.commit()
        node_id = cast(UUID, node.id)
        result = await service.send_message(
            s, engagement_id=eng_id, requester=owner, content="what is running?"
        )
        await s.commit()
        assistant = await chat_repo.get_message_for_owner(
            s, message_id=result.assistant_message.id, user_id=cast(UUID, owner.id)
        )
        assert assistant is not None

    foreign_id = uuid4()
    block = _meta(
        claims=[
            {"text": "real node claim", "certainty": 60, "node_id": str(node_id)},
            {"text": "foreign node claim", "certainty": 40, "node_id": str(foreign_id)},
        ]
    )
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(_tokens("Hi.", block)))

    chunks = [c async for c in service.stream_assistant_reply(message=assistant)]

    done = chunks[-1]
    assert done.claims is not None
    by_text = {c.text: c for c in done.claims}
    assert by_text["real node claim"].node_id == node_id  # known live node kept
    assert by_text["foreign node claim"].node_id is None  # foreign id dropped (§17.1, Risk 3)


@pytest.mark.asyncio
async def test_no_block_yields_empty_plan_and_clean_prose(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Just ", "plain ", "prose."])
    )
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    token_data = "".join(c.data or "" for c in chunks if c.type == "token")
    assert token_data == "Just plain prose."
    done = chunks[-1]
    assert done.type == "done"
    assert done.plan == []
    assert done.claims == []

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.status == "complete"
    assert row.content == "Just plain prose."


@pytest.mark.asyncio
async def test_replay_complete_turn_returns_stored_plan(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        raise AssertionError("Ollama must not be called on replay")

    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom)

    message = await _seed_pending(db_factory)
    async with db_factory() as s:
        await chat_repo.finalize_assistant(
            s,
            message_id=cast(UUID, message.id),
            content="stored prose",
            status="complete",
            model="qwen3.5:9b",
            prompt_tokens=1,
            completion_tokens=1,
            graph_context={
                "plan": [{"step": "stored step", "status": "done"}],
                "claims": [{"text": "stored claim", "certainty": 42, "node_id": None}],
            },
        )
        await s.commit()

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.type == "done"
    assert done.plan is not None and done.plan[0].step == "stored step"
    assert done.claims is not None and done.claims[0].certainty == 42
    mock_audit_record.assert_not_awaited()  # no re-emit on replay (Risk 6)


@pytest.mark.asyncio
async def test_ai_call_payload_has_plan_and_claim_counts(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[{"step": "a", "status": "todo"}, {"step": "b", "status": "done"}],
        claims=[{"text": "c", "certainty": 10}],
    )
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(_tokens("Hi.", block)))
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    mock_audit_record.assert_awaited_once()
    call = mock_audit_record.await_args
    assert call is not None
    payload = call.kwargs["payload"]
    assert payload["plan_steps"] == 2
    assert payload["claims_count"] == 1


@pytest.mark.asyncio
async def test_prose_not_redacted(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "the api key is sk-DO-NOT-STRIP-123"
    block = _meta(claims=[{"text": "unsure", "certainty": 30}])
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(_tokens(f"Note: {secret}", block))
    )
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    token_data = "".join(c.data or "" for c in chunks if c.type == "token")
    assert secret in token_data  # prose passes through verbatim (§5.5 / Risk 5)
    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert secret in row.content


# ---------------------------------------------------------------------------
# Read paths surface stored plan/claims (Slice 13 task 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_includes_plan(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[{"step": "the step", "status": "done"}],
        claims=[{"text": "the claim", "certainty": 50}],
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(_tokens("Answer", block))
    )
    message = await _seed_pending(db_factory)
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        user = await auth_repo.get_user_by_id(s, cast(UUID, message.user_id))
        assert user is not None
        page = await service.list_messages(
            s,
            engagement_id=cast(UUID, message.engagement_id),
            requester=user,
            cursor=None,
            limit=50,
        )

    assistant_rows = [m for m in page.items if m.role == "assistant"]
    assert assistant_rows[0].plan[0].step == "the step"
    assert assistant_rows[0].claims[0].certainty == 50
    # The user row carries no plan/claims.
    user_rows = [m for m in page.items if m.role == "user"]
    assert user_rows[0].plan == []
    assert user_rows[0].claims == []


@pytest.mark.asyncio
async def test_turn_debug_includes_parsed_plan_and_claims(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _meta(
        plan=[{"step": "enumerate", "status": "in_progress"}],
        claims=[{"text": "unsure", "certainty": 33}],
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(_tokens("Visible prose.", block))
    )
    message = await _seed_pending(db_factory)
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        user = await auth_repo.get_user_by_id(s, cast(UUID, message.user_id))
        assert user is not None
        debug = await service.get_turn_debug(
            s,
            engagement_id=cast(UUID, message.engagement_id),
            requester=user,
            message_id=cast(UUID, message.id),
        )

    assert debug.plan[0].step == "enumerate"
    assert debug.claims[0].certainty == 33
    # §14: the debug view shows the UNSTRIPPED output (block included).
    assert "adeptus-meta" in debug.model_output


@pytest.mark.asyncio
async def test_pre_slice_row_reads_empty_plan(db_session: AsyncSession) -> None:
    """A pre-Slice-13 assistant row (graph_context without plan/claims keys) reads empty,
    and its debug model_output falls back to the row content (no block ever existed)."""
    owner = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, owner.id))
    _, assistant = await chat_repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng_id, user_id=cast(UUID, owner.id), content="hi"
    )
    await chat_repo.finalize_assistant(
        db_session,
        message_id=cast(UUID, assistant.id),
        content="old answer",
        status="complete",
        model="qwen3.5:9b",
        prompt_tokens=None,
        completion_tokens=None,
        graph_context={"nodes": [], "edges": [], "context_block": "", "raw_prompt": "x"},
    )
    await db_session.commit()

    page = await service.list_messages(
        db_session, engagement_id=eng_id, requester=owner, cursor=None, limit=50
    )
    arows = [m for m in page.items if m.role == "assistant"]
    assert arows[0].plan == []
    assert arows[0].claims == []

    debug = await service.get_turn_debug(
        db_session, engagement_id=eng_id, requester=owner, message_id=cast(UUID, assistant.id)
    )
    assert debug.plan == []
    assert debug.claims == []
    assert debug.model_output == "old answer"  # fallback to content for pre-slice row
