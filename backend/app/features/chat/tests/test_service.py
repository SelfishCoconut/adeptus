"""Service tests — Ollama client and audit.record are mocked (CLAUDE.md).

Membership / persist / list / WS-auth paths run against a real SQLite session; the
streaming paths run against the ``db_factory`` (the service opens its own session) with
``ollama_client.stream_chat`` replaced by a deterministic fake.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.features.approvals.schemas import (
    ApprovalReason,
    ApprovalRequestRead,
    ApprovalStatus,
    ProposedAction,
)
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.chat import plan_parser, service
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo
from app.features.graph import repository as graph_repo
from app.features.personas import service as personas_service
from app.features.personas.models import Persona as PersonaModel

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


async def _seed_engagement(
    db: AsyncSession, owner_id: UUID, *, archived: bool = False, cloud: bool = False
) -> UUID:
    engagement = await eng_repo.create_engagement(
        db, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
    )
    if archived:
        engagement.status = "archived"
    if cloud:
        engagement.privacy_mode = "cloud_enabled"
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
        proposed: object = None,
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
        proposed: object = None,
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
        proposed: object = None,
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
# send_message — §5.1 cloud egress pattern-friction gate (Slice 14)
# ---------------------------------------------------------------------------

# Synthetic test vectors (not real secrets); each carries gitleaks:allow.
_SECRET = "my key AKIAIOSFODNN7EXAMPLE and password=hunter2"  # gitleaks:allow


async def _stash_of(db: AsyncSession, message_id: UUID, user_id: UUID) -> dict:
    row = await chat_repo.get_message_for_owner(db, message_id=message_id, user_id=user_id)
    assert row is not None
    return cast(dict, row.graph_context)


@pytest.mark.asyncio
async def test_send_cloud_enabled_secret_unconfirmed_raises_egress_409(
    db_session: AsyncSession,
) -> None:
    """A cloud send matching a secret without confirmation is refused before the row exists."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    with pytest.raises(service.EgressConfirmationRequiredError):
        await service.send_message(
            db_session, engagement_id=eng_id, requester=user, content=_SECRET
        )


@pytest.mark.asyncio
async def test_send_cloud_enabled_secret_confirmed_persists_pair(db_session: AsyncSession) -> None:
    """With confirmed_egress=True the friction is satisfied and the pair persists."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    result = await service.send_message(
        db_session,
        engagement_id=eng_id,
        requester=user,
        content=_SECRET,
        confirmed_egress=True,
    )
    await db_session.commit()
    assert result.user_message.status == "complete"
    assert result.assistant_message.status == "pending"


@pytest.mark.asyncio
async def test_send_cloud_enabled_clean_text_no_friction(db_session: AsyncSession) -> None:
    """An ordinary cloud message (no secret) sends with no friction and is not flagged."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    result = await service.send_message(
        db_session, engagement_id=eng_id, requester=user, content="what is SQL injection?"
    )
    await db_session.commit()
    stash = await _stash_of(db_session, result.assistant_message.id, cast(UUID, user.id))
    assert stash["egress"]["secret_flagged"] is False
    assert stash["egress"]["match_categories"] == []


@pytest.mark.asyncio
async def test_send_local_only_secret_never_scanned(db_session: AsyncSession) -> None:
    """A secret on a local_only engagement persists with no friction — no egress to gate (§5.5)."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id))  # local_only (default)

    result = await service.send_message(
        db_session, engagement_id=eng_id, requester=user, content=_SECRET
    )
    await db_session.commit()
    stash = await _stash_of(db_session, result.assistant_message.id, cast(UUID, user.id))
    # Never scanned: the local path has no cloud egress, so nothing is flagged.
    assert stash["egress"]["secret_flagged"] is False
    assert stash["egress"]["match_categories"] == []


@pytest.mark.asyncio
async def test_egress_decision_stashed_on_pending_row(db_session: AsyncSession) -> None:
    """A confirmed flagged send stashes the egress decision for the streamer's audit payload."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    result = await service.send_message(
        db_session,
        engagement_id=eng_id,
        requester=user,
        content=_SECRET,
        confirmed_egress=True,
    )
    await db_session.commit()
    egress = (await _stash_of(db_session, result.assistant_message.id, cast(UUID, user.id)))[
        "egress"
    ]
    assert egress["secret_flagged"] is True
    assert egress["confirmed"] is True
    assert "aws_access_key" in egress["match_categories"]
    assert "password_assignment" in egress["match_categories"]


@pytest.mark.asyncio
async def test_egress_409_body_carries_category_names_not_values(db_session: AsyncSession) -> None:
    """The 409 error carries category NAMES only — never the matched secret value (§5.5)."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    with pytest.raises(service.EgressConfirmationRequiredError) as exc:
        await service.send_message(
            db_session, engagement_id=eng_id, requester=user, content=_SECRET
        )
    categories = exc.value.matched_categories
    assert "aws_access_key" in categories
    # The secret value must not appear anywhere in the raised error.
    assert "AKIAIOSFODNN7EXAMPLE" not in str(exc.value)  # gitleaks:allow
    assert "AKIAIOSFODNN7EXAMPLE" not in "".join(categories)  # gitleaks:allow


@pytest.mark.asyncio
async def test_content_not_redacted_on_confirmed_send(db_session: AsyncSession) -> None:
    """The persisted user content is byte-for-byte the input — never redacted (§5.5 / Risk 2)."""
    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id), cloud=True)

    result = await service.send_message(
        db_session,
        engagement_id=eng_id,
        requester=user,
        content=_SECRET,
        confirmed_egress=True,
    )
    await db_session.commit()
    assert result.user_message.content == _SECRET


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
    # The §5.3 low-confidence threshold (backend tunable) rides on the page (default 70).
    assert owner_page.low_confidence_threshold == 70


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
        proposed: object = None,
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


def test_to_turn_debug_surfaces_persona() -> None:
    """§14 / §17.6: the debug record surfaces the persona that shaped the turn (Slice 15)."""
    pid = uuid4()
    message = ChatMessage(
        id=uuid4(),
        engagement_id=uuid4(),
        user_id=uuid4(),
        role="assistant",
        content="recon answer",
        status="complete",
        model="qwen3.5:9b",
        graph_context={
            "raw_prompt": "[system]\nRECON...",
            "persona_id": str(pid),
            "persona_name": "Recon",
        },
    )
    debug = service._to_turn_debug(message)
    assert debug.persona_id == pid
    assert debug.persona_name == "Recon"


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
async def test_stream_truncated_block_no_leak_and_consistent_persist(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply that opens <adeptus-meta> but never closes it (truncated stream): the
    sentinel must not leak into the streamed tokens, and the stored content must equal the
    pre-marker prose (streamed tokens and persisted prose stay consistent — W4 / Risk 2)."""
    truncated = "Visible answer.\n\n" + plan_parser.START_MARKER + '\n{"plan": [{"step": "half'
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(_chunk(truncated, 5)))
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    token_data = "".join(c.data or "" for c in chunks if c.type == "token")
    assert "adeptus-meta" not in token_data  # sentinel never leaked
    # The streamed prose is exactly the pre-marker prose (no block fragment, no prose lost);
    # it agrees with the stored content modulo the trailing whitespace extract() strips.
    assert token_data.strip() == "Visible answer."
    done = chunks[-1]
    assert done.type == "done"
    assert done.plan == [] and done.claims == []

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    # Streamed prose and stored content agree — no prose lost, no block fragment kept.
    assert row.content == "Visible answer."
    assert "adeptus-meta" not in row.content


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
        proposed: object = None,
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


# ---------------------------------------------------------------------------
# stream_assistant_reply — Slice 14 backend selection (cloud vs local, §5.1)
# ---------------------------------------------------------------------------

_CLOUD_KEY = "sk-ant-test-streamkey"  # gitleaks:allow — synthetic test key, not real
# Synthetic secret vector; carries gitleaks:allow.
_CLOUD_SECRET = "deploy creds AKIAIOSFODNN7EXAMPLE password=hunter2"  # gitleaks:allow


def _boom_stream() -> FakeStream:
    """A stream_chat that fails the test if it is ever iterated (asserts the OTHER backend)."""

    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
        proposed: object = None,
    ) -> AsyncIterator[str]:
        raise AssertionError("this backend must not be called")
        yield ""  # pragma: no cover — makes _gen a generator

    return _gen


async def _seed_cloud_pending(
    factory: async_sessionmaker[AsyncSession],
    *,
    content: str = "what is sqli?",
    confirmed_egress: bool = False,
) -> ChatMessage:
    """Seed a pending turn on a cloud_enabled engagement via the real send_message path.

    Goes through the POST gate so the egress stash is populated exactly as in production."""
    async with factory() as s:
        owner = await _seed_user(s, f"owner-{uuid4().hex[:8]}")
        eng_id = await _seed_engagement(s, cast(UUID, owner.id), cloud=True)
        result = await service.send_message(
            s,
            engagement_id=eng_id,
            requester=owner,
            content=content,
            confirmed_egress=confirmed_egress,
        )
        await s.commit()
        assistant = await chat_repo.get_message_for_owner(
            s, message_id=result.assistant_message.id, user_id=cast(UUID, owner.id)
        )
        assert assistant is not None
        return assistant


def _set_cloud_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_API_KEY", _CLOUD_KEY)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stream_cloud_engagement_uses_anthropic_client(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _fake_stream(["Hel", "lo"]))
    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom_stream())
    message = await _seed_cloud_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert [c.data for c in chunks if c.type == "token"] == ["Hel", "lo"]
    assert chunks[-1].type == "done"
    # The row + audit reflect the real backend (Claude model, not qwen).
    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_stream_local_engagement_uses_ollama_client(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_cloud_key(monkeypatch)  # key present, but the engagement is local_only → still local
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["lo", "cal"]))
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _boom_stream())
    message = await _seed_pending(db_factory)  # local_only (default)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert [c.data for c in chunks if c.type == "token"] == ["lo", "cal"]
    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.model == "qwen3.5:9b"


@pytest.mark.asyncio
async def test_stream_cloud_without_key_finalizes_failed_no_fallback(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_enabled + no key → the turn fails; it never silently uses local (§5.1, Risk 6)."""
    monkeypatch.delenv("ADEPTUS_ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom_stream())
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _boom_stream())
    message = await _seed_cloud_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].message == service.CLOUD_NOT_CONFIGURED_MESSAGE
    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.status == "failed"
    # Exactly one ai_call, status=failed, backend=cloud — and neither client was iterated.
    mock_audit_record.assert_awaited_once()
    payload = mock_audit_record.await_args.kwargs["payload"]  # type: ignore[union-attr]
    assert payload["status"] == "failed"
    assert payload["backend"] == "cloud"


@pytest.mark.asyncio
async def test_ai_call_payload_records_backend_and_egress_decision(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _fake_stream(["ok"]))
    message = await _seed_cloud_pending(db_factory, content=_CLOUD_SECRET, confirmed_egress=True)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    mock_audit_record.assert_awaited_once()
    payload = mock_audit_record.await_args.kwargs["payload"]  # type: ignore[union-attr]
    assert payload["backend"] == "cloud"
    assert payload["egress_secret_flagged"] is True
    assert payload["egress_confirmed"] is True
    assert "aws_access_key" in payload["egress_match_categories"]
    # The category NAMES are recorded — never the matched secret value (§5.5 / Risk 7).
    assert "AKIAIOSFODNN7EXAMPLE" not in str(payload)  # gitleaks:allow


@pytest.mark.asyncio
async def test_cloud_turn_records_token_counts(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(
        service.anthropic_client,
        "stream_chat",
        _fake_stream(["hi"], prompt_tokens=42, completion_tokens=7),
    )
    message = await _seed_cloud_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    assert row.prompt_tokens == 42
    assert row.completion_tokens == 7


@pytest.mark.asyncio
async def test_cloud_path_does_not_redact_content(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window passed to the cloud client is the verbatim user content (§5.5 / Risk 2)."""
    _set_cloud_key(monkeypatch)
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message = await _seed_cloud_pending(db_factory, content=_CLOUD_SECRET, confirmed_egress=True)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    # The secret-bearing user turn reaches the cloud client byte-for-byte (never redacted).
    user_turns = [m.content for m in captured[0] if m.role == "user"]
    assert any(_CLOUD_SECRET in c for c in user_turns)


async def _set_privacy_mode(
    factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
    user_id: UUID,
    mode: Literal["local_only", "cloud_enabled"],
) -> None:
    """Flip an engagement's privacy mode mid-flight (mimics the owner PATCH, Slice 02)."""
    async with factory() as s:
        member = await eng_repo.get_engagement_for_member(s, engagement_id, user_id)
        assert member is not None
        member[0].privacy_mode = mode
        await s.commit()


@pytest.mark.asyncio
async def test_stream_cloud_flip_rescans_and_refuses_unconfirmed_secret(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOCTOU guard (Risk 1): a secret POSTed under local_only then streamed after a flip to
    cloud_enabled is re-scanned at the egress point and refused — it never reaches the cloud,
    and the audit honestly records the flagged failure."""
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _boom_stream())
    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom_stream())

    async with db_factory() as s:
        owner = await _seed_user(s, f"owner-{uuid4().hex[:8]}")
        owner_id = cast(UUID, owner.id)
        eng_id = await _seed_engagement(s, owner_id)  # local_only — no friction at POST
        result = await service.send_message(
            s, engagement_id=eng_id, requester=owner, content=_CLOUD_SECRET
        )
        await s.commit()
        assistant_id = result.assistant_message.id

    # Owner flips the engagement to cloud_enabled AFTER the secret was persisted.
    await _set_privacy_mode(db_factory, eng_id, owner_id, "cloud_enabled")

    async with db_factory() as s:
        assistant = await chat_repo.get_message_for_owner(
            s, message_id=assistant_id, user_id=owner_id
        )
    assert assistant is not None
    chunks = [c async for c in service.stream_assistant_reply(message=assistant)]

    # Refused before any token left the machine; neither client was iterated (_boom_stream).
    assert [c.type for c in chunks] == ["error"]
    assert chunks[0].message == service.EGRESS_UNCONFIRMED_MESSAGE

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(s, message_id=assistant_id, user_id=owner_id)
    assert row is not None
    assert row.status == "failed"

    # The audit honestly records the flagged, unconfirmed cloud refusal (category NAMES only).
    mock_audit_record.assert_awaited_once()
    payload = mock_audit_record.await_args.kwargs["payload"]  # type: ignore[union-attr]
    assert payload["status"] == "failed"
    assert payload["backend"] == "cloud"
    assert payload["egress_secret_flagged"] is True
    assert payload["egress_confirmed"] is False
    assert "aws_access_key" in payload["egress_match_categories"]
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(payload)  # gitleaks:allow


@pytest.mark.asyncio
async def test_stream_cloud_flip_allows_confirmed_secret(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secret CONFIRMED at the POST (cloud_enabled) still streams after the re-scan — the
    re-check refuses only UNCONFIRMED matches, so confirmed sends are not double-gated."""
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _fake_stream(["ok"]))
    message = await _seed_cloud_pending(db_factory, content=_CLOUD_SECRET, confirmed_egress=True)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert chunks[-1].type == "done"


# ---------------------------------------------------------------------------
# Persona threading (Slice 15) — send_message resolve/stash + stream prompt/audit
# ---------------------------------------------------------------------------


async def _seed_persona_turn(
    factory: async_sessionmaker[AsyncSession],
    *,
    content: str = "where should I start?",
    persona_prompt: str | None = None,
    persona_name: str = "Recon X",
    use_foreign: bool = False,
    foreign_prompt: str = "FOREIGN-SECRET-PROMPT",
    seed_builtins: bool = True,
    node_specs: Sequence[tuple[str, str, dict[str, object]]] = (),
    pin_labels: Sequence[str] = (),
) -> tuple[ChatMessage, dict[str, object]]:
    """Seed a turn through ``send_message`` with a selected persona; return (row, info).

    ``persona_prompt`` creates + selects a custom persona owned by the sender; ``use_foreign``
    selects another user's persona id (the §17.1 fallback case). Goes through the real
    send path so the persona is resolved + stashed exactly as in production.
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
        if seed_builtins:
            await personas_service.bootstrap_system_personas(s)
        persona_id: UUID | None = None
        info: dict[str, object] = {"owner_id": cast(UUID, owner.id)}
        if persona_prompt is not None:
            created = await personas_service.create_persona(
                s, requester=owner, name=persona_name, system_prompt=persona_prompt
            )
            persona_id = created.id
            info["persona_id"] = created.id
            info["persona_name"] = persona_name
        if use_foreign:
            other = await _seed_user(s, f"other-{uuid4().hex[:8]}")
            others = await personas_service.create_persona(
                s, requester=other, name="Bobs", system_prompt=foreign_prompt
            )
            persona_id = others.id
            info["foreign_persona_id"] = others.id
        await s.commit()
        result = await service.send_message(
            s,
            engagement_id=eng_id,
            requester=owner,
            content=content,
            persona_id=persona_id,
            pinned_node_ids=[label_to_id[label] for label in pin_labels],
        )
        await s.commit()
        assistant = await chat_repo.get_message_for_owner(
            s, message_id=result.assistant_message.id, user_id=cast(UUID, owner.id)
        )
        assert assistant is not None
        info["assistant_id"] = cast(UUID, assistant.id)
        return assistant, info


@pytest.mark.asyncio
async def test_send_resolves_and_stashes_persona(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    message, info = await _seed_persona_turn(db_factory, persona_prompt="RECON-FOCUS")
    gc = message.graph_context
    assert gc is not None
    assert gc["persona"]["id"] == str(info["persona_id"])
    assert gc["persona"]["name"] == "Recon X"


@pytest.mark.asyncio
async def test_send_foreign_persona_falls_back_to_general(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§17.1 — a foreign persona id is resolved to general at POST, never the foreign prompt."""
    message, _info = await _seed_persona_turn(db_factory, use_foreign=True)
    gc = message.graph_context
    assert gc is not None
    assert gc["persona"]["name"] == "General"


@pytest.mark.asyncio
async def test_prompt_uses_persona_system_prompt(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message, _info = await _seed_persona_turn(
        db_factory, persona_prompt="RECON-FOCUS: enumerate first."
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    system_content = captured[0][0].content
    assert system_content.startswith("RECON-FOCUS: enumerate first.")
    # The old fixed neutral prompt is NOT the base term anymore.
    assert not system_content.startswith(service.SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_prompt_composes_persona_then_context_then_instruction(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message, _info = await _seed_persona_turn(
        db_factory,
        content="what about the UNIQUELABEL endpoint?",
        persona_prompt="PERSONA-BASE-PROMPT",
        node_specs=[("endpoint", "UNIQUELABEL", {})],
        pin_labels=["UNIQUELABEL"],
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    content = captured[0][0].content
    persona_at = content.index("PERSONA-BASE-PROMPT")
    context_at = content.index("UNIQUELABEL")
    instruction_at = content.index(service.PLAN_CERTAINTY_INSTRUCTION)
    # persona prompt → graph context block → structured-output instruction (order preserved).
    assert persona_at < context_at < instruction_at


@pytest.mark.asyncio
async def test_default_general_prompt_byte_equal_to_legacy(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-persona send (built-ins seeded) uses the general built-in == the legacy prompt."""
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message, _info = await _seed_persona_turn(db_factory, persona_prompt=None)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    assert captured[0][0].content == service.SYSTEM_PROMPT + service.PLAN_CERTAINTY_INSTRUCTION


@pytest.mark.asyncio
async def test_persona_recorded_on_turn_and_in_audit(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["done"]))
    message, info = await _seed_persona_turn(
        db_factory, persona_prompt="RECON", persona_name="Recon X"
    )

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with db_factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=cast(UUID, message.id), user_id=cast(UUID, message.user_id)
        )
    assert row is not None
    gc = row.graph_context
    assert gc is not None
    assert gc["persona_id"] == str(info["persona_id"])
    assert gc["persona_name"] == "Recon X"

    mock_audit_record.assert_awaited_once()
    payload = mock_audit_record.await_args.kwargs["payload"]  # type: ignore[union-attr]
    assert payload["persona_id"] == str(info["persona_id"])
    assert payload["persona_name"] == "Recon X"


@pytest.mark.asyncio
async def test_persona_deleted_between_post_and_stream_falls_back(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persona deleted after POST but before the stream re-resolves to general (Risk 6)."""
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message, info = await _seed_persona_turn(db_factory, persona_prompt="DELETED-PROMPT")

    # Delete the persona out from under the pending turn.
    async with db_factory() as s:
        await s.execute(delete(PersonaModel))
        await s.commit()

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    # Falls back to general (the built-ins were also deleted → synthesized general prompt).
    assert captured[0][0].content.startswith(service.SYSTEM_PROMPT)
    assert "DELETED-PROMPT" not in captured[0][0].content


@pytest.mark.asyncio
async def test_persona_prompt_not_redacted(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5.5 — a secret-looking persona prompt is forwarded verbatim, never stripped/rewritten."""
    secret_prompt = "Use the key AKIAIOSFODNN7EXAMPLE when reasoning."  # gitleaks:allow
    captured: list[Sequence[OllamaChatMessage]] = []
    monkeypatch.setattr(service.ollama_client, "stream_chat", _capture_prompt(captured, ["ok"]))
    message, _info = await _seed_persona_turn(db_factory, persona_prompt=secret_prompt)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    assert secret_prompt in captured[0][0].content


# ---------------------------------------------------------------------------
# Slice 16: AI-proposed action routing (native tool-calls → classify/gate/run)
# ---------------------------------------------------------------------------


def _akw(mock: AsyncMock) -> dict[str, object]:
    """Guarded kwargs of a mock's awaited call (keeps mypy happy on await_args)."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _fake_stream_with_tool_call(
    tokens: list[str],
    *,
    server: str = "shell-exec",
    tool: str = "run",
    args: Mapping[str, object] | None = None,
) -> FakeStream:
    """A fake stream that yields prose AND populates the native tool-call holder."""
    from app.features.chat.tool_calling import ProposedCalls, ProposedToolCall

    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
        proposed: object = None,
    ) -> AsyncIterator[str]:
        for tok in tokens:
            yield tok
        if isinstance(proposed, ProposedCalls):
            proposed.calls.append(
                ProposedToolCall(
                    name="propose_command",
                    arguments={
                        "server": server,
                        "tool": tool,
                        "args": args if args is not None else {"cmd": "id"},
                    },
                )
            )

    return _gen


def _mock_classify(
    service_module: object,
    monkeypatch: pytest.MonkeyPatch,
    *,
    autonomous: list[ProposedAction] | None = None,
    gated: list[ApprovalRequestRead] | None = None,
) -> AsyncMock:
    from app.features.approvals.service import ClassifiedTurnResult

    mock = AsyncMock(
        return_value=ClassifiedTurnResult(autonomous=autonomous or [], gated=gated or [])
    )
    monkeypatch.setattr(service.approvals_service, "create_requests_for_turn", mock)
    return mock


def _approval_read(server: str = "shell-exec") -> ApprovalRequestRead:
    return ApprovalRequestRead(
        id=uuid4(),
        engagement_id=uuid4(),
        chat_message_id=uuid4(),
        initiator_user_id=uuid4(),
        server_name=server,
        tool_name="run",
        args={"cmd": "hydra"},
        reasons=[ApprovalReason.CREDENTIAL_ATTACK],
        status=ApprovalStatus.PENDING,
        created_at=datetime.datetime(2026, 6, 5, tzinfo=datetime.UTC),
    )


@pytest.mark.asyncio
async def test_turn_parses_native_tool_calls(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.ollama_client,
        "stream_chat",
        _fake_stream_with_tool_call(["Running "], server="httpx-server", tool="httpx"),
    )
    classify = _mock_classify(service, monkeypatch)
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    classify.assert_awaited_once()
    actions = cast("list[ProposedAction]", _akw(classify)["actions"])
    assert len(actions) == 1
    assert actions[0].server_name == "httpx-server"
    assert actions[0].tool_name == "httpx"


@pytest.mark.asyncio
async def test_no_tool_call_degrades_cleanly(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["just prose"]))
    classify = _mock_classify(service, monkeypatch)
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    assert [c.type for c in chunks] == ["token", "done"]
    assert _akw(classify)["actions"] == []
    assert chunks[-1].approval_requests == []


@pytest.mark.asyncio
async def test_gated_action_emits_proposed_action_frame(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream_with_tool_call(["x"]))
    gated = [_approval_read()]
    _mock_classify(service, monkeypatch, gated=gated)
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    proposed_frames = [c for c in chunks if c.type == "proposed_action"]
    assert len(proposed_frames) == 1
    assert proposed_frames[0].approval_request is not None
    assert proposed_frames[0].approval_request.reasons[0].value == "credential_attack"


@pytest.mark.asyncio
async def test_done_frame_repeats_approval_requests(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream_with_tool_call(["x"]))
    gated = [_approval_read()]
    _mock_classify(service, monkeypatch, gated=gated)
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.type == "done"
    assert done.approval_requests is not None
    assert len(done.approval_requests) == 1


@pytest.mark.asyncio
async def test_autonomous_action_runs_immediately_and_emits_frame(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.features.approvals.schemas import ProposedAction

    monkeypatch.setattr(
        service.ollama_client,
        "stream_chat",
        _fake_stream_with_tool_call(["x"], server="httpx-server", tool="httpx"),
    )
    autonomous = [
        ProposedAction(
            server_name="httpx-server",
            tool_name="httpx",
            args={"target": "10.0.0.5"},
            rationale="recon",
        )
    ]
    _mock_classify(service, monkeypatch, autonomous=autonomous)
    run_id = uuid4()
    exec_mock = AsyncMock(return_value=SimpleNamespace(tool_run_id=run_id))
    monkeypatch.setattr(service.mcp_service, "execute_tool_run", exec_mock)
    message = await _seed_pending(db_factory)

    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    exec_mock.assert_awaited_once()
    # The run is attributed to the initiator (turn owner — Resolved decision 3).
    assert _akw(exec_mock)["user_id"] == cast(UUID, message.user_id)
    auto_frames = [c for c in chunks if c.type == "proposed_action" and c.autonomous_action]
    assert len(auto_frames) == 1
    card = auto_frames[0].autonomous_action
    assert card is not None and card.tool_run_id == run_id


@pytest.mark.asyncio
async def test_action_args_not_redacted(
    db_factory: async_sessionmaker[AsyncSession],
    mock_audit_record: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = {"cmd": "hydra -l admin -p Sup3rSecret! ssh://10.0.0.5"}
    monkeypatch.setattr(
        service.ollama_client,
        "stream_chat",
        _fake_stream_with_tool_call(["x"], args=secret),
    )
    classify = _mock_classify(service, monkeypatch)
    message = await _seed_pending(db_factory)

    _ = [c async for c in service.stream_assistant_reply(message=message)]

    sent_actions = cast("list[ProposedAction]", _akw(classify)["actions"])
    assert sent_actions[0].args == secret  # verbatim (§5.5)


@pytest.mark.asyncio
async def test_list_messages_includes_approval_requests(
    db_session: AsyncSession,
) -> None:
    from app.features.approvals import repository as approvals_repo

    user = await _seed_user(db_session, "owner")
    eng_id = await _seed_engagement(db_session, cast(UUID, user.id))
    _, assistant = await chat_repo.insert_user_and_pending_assistant(
        db_session, engagement_id=eng_id, user_id=cast(UUID, user.id), content="hi"
    )
    await db_session.commit()
    await approvals_repo.create_request(
        db_session,
        engagement_id=eng_id,
        chat_message_id=cast(UUID, assistant.id),
        initiator_user_id=cast(UUID, user.id),
        server_name="shell-exec",
        tool_name="run",
        args={"cmd": "hydra"},
        reasons=["credential_attack"],
    )
    await db_session.commit()

    page = await service.list_messages(
        db_session, engagement_id=eng_id, requester=user, cursor=None, limit=50
    )
    assistant_reads = [m for m in page.items if m.role == "assistant"]
    assert len(assistant_reads) == 1
    assert len(assistant_reads[0].approval_requests) == 1
    assert assistant_reads[0].approval_requests[0].server_name == "shell-exec"
