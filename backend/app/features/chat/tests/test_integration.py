"""Integration tests for private chat against a real Postgres (Slice 11).

These cover what the SQLite unit tests cannot: the real timestamptz/JSONB round-trip, the
CHECK constraints, and the genuine FOR UPDATE audit-chain append firing from the streaming
orchestration. **Ollama is still mocked** — external services are never hit in tests
(CLAUDE.md); only the local DB is real.

Each test runs against a throwaway Postgres schema (mirrors audit/graph integration) and
skips cleanly when Postgres is unreachable. Marked ``integration`` — excluded from the
default ``make test-backend`` run; executed by ``make test-integration``. Point at a
server with ``ADEPTUS_TEST_DATABASE_URL`` (defaults to the compose Postgres).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import cast
from unittest.mock import patch

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_engine, get_sessionmaker
from app.core.errors import NotFoundError
from app.features.audit.models import AuditChainHead, AuditEntry
from app.features.auth import repository as auth_repo
from app.features.auth.models import Session, User
from app.features.chat import plan_parser, service
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement, EngagementMember
from app.features.graph import repository as graph_repo
from app.features.graph.models import GraphEdge, GraphNode

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_hasher = PasswordHasher()

# Exactly the tables these tests touch (auth + engagements + chat + audit + graph).
_TABLES: list[Table] = [
    cast(Table, model.__table__)
    for model in (
        User,
        Session,
        Engagement,
        EngagementMember,
        ChatMessage,
        AuditEntry,
        AuditChainHead,
        GraphNode,
        GraphEdge,
    )
]

# A fake stream_chat: yields str tokens, optionally populating the usage holder.
FakeStream = Callable[..., AsyncIterator[str]]


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


@pytest_asyncio.fixture
async def pg_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory scoped to a throwaway Postgres schema, with the service's
    ``get_sessionmaker`` patched to it (streaming opens its own session). Skips if
    Postgres is down."""
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"chat_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(_dsn(), connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        with patch("app.features.chat.service.get_sessionmaker", return_value=factory):
            yield factory
    finally:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


# ---------------------------------------------------------------------------
# Fake Ollama streams
# ---------------------------------------------------------------------------


def _fake_stream(tokens: list[str]) -> FakeStream:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        for tok in tokens:
            yield tok
        if usage is not None:
            usage.prompt_tokens = 7
            usage.completion_tokens = len(tokens)

    return _gen


def _meta(
    *,
    plan: list[dict[str, object]] | None = None,
    claims: list[dict[str, object]] | None = None,
) -> str:
    """Render a well-formed trailing <adeptus-meta> block (Slice 13)."""
    payload = {"plan": plan or [], "claims": claims or []}
    return f"{plan_parser.START_MARKER}\n{json.dumps(payload)}\n{plan_parser.END_MARKER}"


def _fake_unreachable() -> FakeStream:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        if False:  # pragma: no cover
            yield ""
        raise service.LlmUnreachableError("boom")

    return _gen


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(factory: async_sessionmaker[AsyncSession], username: str) -> User:
    async with factory() as db:
        user = await auth_repo.create_user(
            db, username=username, password_hash=_hasher.hash("pw"), role="user"
        )
        await db.commit()
        await db.refresh(user)
        return user


async def _seed_engagement(
    factory: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, *, cloud: bool = False
) -> uuid.UUID:
    async with factory() as db:
        engagement = await eng_repo.create_engagement(
            db, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
        )
        if cloud:
            engagement.privacy_mode = "cloud_enabled"
        await db.commit()
        await db.refresh(engagement)
        return cast(uuid.UUID, engagement.id)


def _boom_stream() -> FakeStream:
    """A stream_chat that fails the test if iterated — asserts the OTHER backend ran."""

    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        raise AssertionError("this backend must not be called")
        yield ""  # pragma: no cover

    return _gen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_chat_round_trip_persists_and_streams(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline §5.4 + §14 happy path: POST → stream → finalize + one ai_call entry."""
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["It ", "is ", "SQLi."]))

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="what is sqli?"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None

    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert [c.type for c in chunks] == ["token", "token", "token", "done"]
    assert "".join(c.data or "" for c in chunks if c.type == "token") == "It is SQLi."

    # The assistant row finalized complete with the joined content.
    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "complete"
        assert row.content == "It is SQLi."
        assert row.model == "qwen3.5:9b"

        # Exactly one ai_call audit entry, attributed to the user, status=complete.
        audit_rows = (
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    assert audit_rows[0].engagement_id == eng_id
    assert audit_rows[0].payload["status"] == "complete"
    assert audit_rows[0].payload["model"] == "qwen3.5:9b"


async def test_chat_private_per_user(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two members of one engagement each have an isolated conversation (§5.4)."""
    owner = await _seed_user(pg_factory, "owner")
    other = await _seed_user(pg_factory, "other")
    eng_id = await _seed_engagement(pg_factory, cast(uuid.UUID, owner.id))
    async with pg_factory() as db:
        await eng_repo.add_member(db, engagement_id=eng_id, user_id=cast(uuid.UUID, other.id))
        await db.commit()

    async with pg_factory() as db:
        await service.send_message(
            db, engagement_id=eng_id, requester=owner, content="owner-secret"
        )
        await service.send_message(
            db, engagement_id=eng_id, requester=other, content="other-secret"
        )
        await db.commit()

    async with pg_factory() as db:
        owner_page = await service.list_messages(
            db, engagement_id=eng_id, requester=owner, cursor=None, limit=50
        )
    contents = [m.content for m in owner_page.items]
    assert "owner-secret" in contents
    assert "other-secret" not in contents


async def test_chat_unreachable_marks_failed(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama unreachable → assistant row failed, error frame, ai_call status=failed."""
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_unreachable())

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        result = await service.send_message(db, engagement_id=eng_id, requester=user, content="hi")
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None

    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert chunks[-1].type == "error"

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "failed"

        audit_rows = (
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload["status"] == "failed"


# ---------------------------------------------------------------------------
# §5.3 relevant subset + §14 debug panel (Slice 12)
# ---------------------------------------------------------------------------


async def test_turn_injects_relevant_subset_and_persists_debug(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline §5.3 + §14 happy path: a pinned node and a keyword match both reach the
    prompt, the debug record persists them with correct reasons, and the single ai_call
    entry carries the subset counts (real Postgres JSONB round-trip)."""
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Try ", "default ", "creds."])
    )
    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        host = await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="host", label="10.0.0.5", properties={}
        )
        await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="endpoint", label="/login", properties={}
        )
        await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="service", label="nginx", properties={}
        )
        await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="vulnerability", label="weak-creds", properties={}
        )
        await db.commit()
        host_id = cast(uuid.UUID, host.id)

    async with pg_factory() as db:
        result = await service.send_message(
            db,
            engagement_id=eng_id,
            requester=user,
            content="what should I try against the /login endpoint?",
            pinned_node_ids=[host_id],
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert chunks[-1].type == "done"

    async with pg_factory() as db:
        debug = await service.get_turn_debug(
            db, engagement_id=eng_id, requester=user, message_id=assistant_id
        )
    by_label = {n.label: n for n in debug.nodes}
    # Only the pinned host and the keyword-matched endpoint were selected.
    assert set(by_label) == {"10.0.0.5", "/login"}
    assert "pinned" in by_label["10.0.0.5"].reasons
    assert "keyword" in by_label["/login"].reasons
    assert "10.0.0.5" in debug.context_block and "/login" in debug.context_block
    assert "10.0.0.5" in debug.raw_prompt
    assert debug.model_output == "Try default creds."

    async with pg_factory() as db:
        audit_rows = (
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload["graph_nodes_injected"] == 2
    assert audit_rows[0].payload["graph_edges_injected"] == 0


async def test_debug_private_per_user(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A second member of the same engagement cannot read the first user's turn debug
    (404, §5.4 / §17.1 / Risk 5)."""
    owner = await _seed_user(pg_factory, "owner")
    other = await _seed_user(pg_factory, "other")
    eng_id = await _seed_engagement(pg_factory, cast(uuid.UUID, owner.id))
    async with pg_factory() as db:
        await eng_repo.add_member(db, engagement_id=eng_id, user_id=cast(uuid.UUID, other.id))
        await db.commit()

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=owner, content="private"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        with pytest.raises(NotFoundError):
            await service.get_turn_debug(
                db, engagement_id=eng_id, requester=other, message_id=assistant_id
            )


async def test_full_subset_injected_end_to_end(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed nodes spanning all four union arms; assert the debug record represents EVERY
    selected node verbatim (no node dropped, no summarization — the full-subset decision)."""
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["ok"]))
    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        pinned = await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="host", label="alpha-pinned", properties={}
        )
        recent = await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="service", label="bravo-recent", properties={}
        )
        mentioned = await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="endpoint", label="charlie-mentioned", properties={}
        )
        await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="vulnerability", label="delta-sqli", properties={}
        )
        await db.commit()
        pinned_id = cast(uuid.UUID, pinned.id)
        recent_id = cast(uuid.UUID, recent.id)
        mentioned_id = cast(uuid.UUID, mentioned.id)

    async with pg_factory() as db:
        result = await service.send_message(
            db,
            engagement_id=eng_id,
            requester=user,
            content="anything about delta-sqli here?",
            pinned_node_ids=[pinned_id],
            recent_node_ids=[recent_id],
            mentioned_node_ids=[mentioned_id],
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with pg_factory() as db:
        debug = await service.get_turn_debug(
            db, engagement_id=eng_id, requester=user, message_id=assistant_id
        )
    labels = {n.label for n in debug.nodes}
    assert labels == {"alpha-pinned", "bravo-recent", "charlie-mentioned", "delta-sqli"}
    # Every selected node appears verbatim in the rendered block (none dropped/summarized).
    for label in labels:
        assert label in debug.context_block
    reasons = {n.label: set(n.reasons) for n in debug.nodes}
    assert "pinned" in reasons["alpha-pinned"]
    assert "recent" in reasons["bravo-recent"]
    assert "mentioned" in reasons["charlie-mentioned"]
    assert "keyword" in reasons["delta-sqli"]


# ---------------------------------------------------------------------------
# §5.3 visible plan + uncertainty signaling (Slice 13)
# ---------------------------------------------------------------------------


async def test_turn_parses_plan_and_persists(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline §5.3 + §14 happy path: a faked reply with a well-formed metadata block →
    stored content is block-stripped prose, the plan (3 steps) + a node-referencing claim
    persist (real JSONB round-trip), the done frame carries them, and the single ai_call
    entry records plan_steps=3 / claims_count=1."""
    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        node = await graph_repo.insert_node(
            db, engagement_id=eng_id, node_type="service", label="apache", properties={}
        )
        await db.commit()
        node_id = cast(uuid.UUID, node.id)

    block = _meta(
        plan=[
            {"step": "Enumerate the login endpoint", "status": "done"},
            {"step": "Test for SQL injection", "status": "in_progress"},
            {"step": "Check session-cookie flags", "status": "todo"},
        ],
        claims=[{"text": "service is likely Apache", "certainty": 60, "node_id": str(node_id)}],
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Here is the approach.\n\n", block])
    )

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="how should I test the login flow?"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.type == "done"
    assert done.plan is not None and len(done.plan) == 3
    assert done.claims is not None and done.claims[0].node_id == node_id
    token_data = "".join(c.data or "" for c in chunks if c.type == "token")
    assert "adeptus-meta" not in token_data  # raw block never streamed

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "complete"
        assert row.content == "Here is the approach."  # block-stripped prose
        gc = row.graph_context
        assert gc is not None
        assert len(gc["plan"]) == 3
        assert gc["claims"][0]["node_id"] == str(node_id)  # survived validation (§17.1)
        assert "adeptus-meta" in gc["model_output"]  # unstripped output kept for §14

        audit_rows = (
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload["plan_steps"] == 3
    assert audit_rows[0].payload["claims_count"] == 1


async def test_turn_with_foreign_node_id_drops_it(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim referencing a node id NOT in this engagement keeps its text but loses the
    node_id (§17.1 / Risk 3) — the Graph-pane badge can never point at a foreign node."""
    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    foreign_id = uuid.uuid4()
    block = _meta(claims=[{"text": "foreign claim", "certainty": 40, "node_id": str(foreign_id)}])
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["Answer.\n", block]))

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="anything?"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.claims is not None
    assert done.claims[0].text == "foreign claim"  # text survives
    assert done.claims[0].node_id is None  # foreign id dropped

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        gc = row.graph_context
        assert gc is not None
        assert gc["claims"][0]["node_id"] is None


async def test_turn_without_block_degrades_cleanly(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply with no metadata block → clean prose persisted, empty plan/claims, the turn
    still completes, and the ai_call carries zero plan/claim counts (Risk 1 graceful)."""
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Just plain prose, ", "no block."])
    )
    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="what port does https use?"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]

    done = chunks[-1]
    assert done.type == "done"
    assert done.plan == []
    assert done.claims == []

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "complete"
        assert row.content == "Just plain prose, no block."

        audit_rows = (
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload["plan_steps"] == 0
    assert audit_rows[0].payload["claims_count"] == 0


async def test_plan_private_per_user(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One member's plan/claims never appear in another member's own history (§5.4 / §17.1)."""
    owner = await _seed_user(pg_factory, "owner")
    other = await _seed_user(pg_factory, "other")
    eng_id = await _seed_engagement(pg_factory, cast(uuid.UUID, owner.id))
    async with pg_factory() as db:
        await eng_repo.add_member(db, engagement_id=eng_id, user_id=cast(uuid.UUID, other.id))
        await db.commit()

    block = _meta(
        plan=[{"step": "owner-only-step", "status": "done"}],
        claims=[{"text": "owner-only-claim", "certainty": 55}],
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Owner answer.\n", block])
    )

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=owner, content="owner question"
        )
        await db.commit()
    assistant_id = result.assistant_message.id
    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=cast(uuid.UUID, owner.id)
        )
    assert message is not None
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    # The owner sees their own plan/claims.
    async with pg_factory() as db:
        owner_page = await service.list_messages(
            db, engagement_id=eng_id, requester=owner, cursor=None, limit=50
        )
    owner_steps = [s.step for m in owner_page.items for s in m.plan]
    assert "owner-only-step" in owner_steps

    # The other member's own history carries none of the owner's plan/claims.
    async with pg_factory() as db:
        other_page = await service.list_messages(
            db, engagement_id=eng_id, requester=other, cursor=None, limit=50
        )
    other_steps = [s.step for m in other_page.items for s in m.plan]
    other_claims = [c for m in other_page.items for c in m.claims]
    assert "owner-only-step" not in other_steps
    assert other_claims == []


# ---------------------------------------------------------------------------
# Slice 14 — cloud backend + pattern-friction egress (§5.1 / §5.5 / §14)
# ---------------------------------------------------------------------------

# Synthetic secret vector (not a real credential); carries gitleaks:allow.
_SECRET = "deploy with AKIAIOSFODNN7EXAMPLE and password=hunter2"  # gitleaks:allow


def _set_cloud_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_API_KEY", "sk-ant-it-key")  # gitleaks:allow
    get_settings.cache_clear()


async def _ai_call_rows(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> list[AuditEntry]:
    async with factory() as db:
        return list(
            (
                await db.execute(
                    select(AuditEntry).where(
                        AuditEntry.action == "ai_call", AuditEntry.actor_user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_cloud_turn_routes_to_anthropic_and_audits(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline §5.1 cloud happy-path: a clean cloud send streams from Claude + audits cloud."""
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _fake_stream(["Cloud ", "reply."]))
    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom_stream())

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id, cloud=True)

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="what is sqli?"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert [c.type for c in chunks] == ["token", "token", "done"]
    assert "".join(c.data or "" for c in chunks if c.type == "token") == "Cloud reply."

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "complete"
        assert row.content == "Cloud reply."
        assert row.model == "claude-sonnet-4-6"

    rows = await _ai_call_rows(pg_factory, user_id)
    assert len(rows) == 1
    assert rows[0].payload["backend"] == "cloud"
    assert rows[0].payload["egress_secret_flagged"] is False


async def test_cloud_secret_unconfirmed_blocks_then_confirmed_sends_unmodified(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline §5.1 friction + §5.5 no-redaction: blocked unconfirmed, then sent byte-for-byte."""
    _set_cloud_key(monkeypatch)
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _fake_stream(["ok"]))

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id, cloud=True)

    # Unconfirmed secret-bearing send → friction 409, and nothing is persisted.
    async with pg_factory() as db:
        with pytest.raises(service.EgressConfirmationRequiredError) as exc:
            await service.send_message(db, engagement_id=eng_id, requester=user, content=_SECRET)
        await db.rollback()
    assert "aws_access_key" in exc.value.matched_categories

    async with pg_factory() as db:
        page = await service.list_messages(
            db, engagement_id=eng_id, requester=user, cursor=None, limit=50
        )
    assert page.items == []  # nothing persisted on the blocked send

    # Re-send confirmed → persisted byte-for-byte (no redaction, §5.5).
    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content=_SECRET, confirmed_egress=True
        )
        await db.commit()
    assistant_id = result.assistant_message.id
    assert result.user_message.content == _SECRET  # byte-for-byte

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        # The stored USER content is byte-for-byte the input.
        user_row = await chat_repo.get_message_for_owner(
            db, message_id=result.user_message.id, user_id=user_id
        )
        assert user_row is not None
        assert user_row.content == _SECRET

    rows = await _ai_call_rows(pg_factory, user_id)
    assert len(rows) == 1
    assert rows[0].payload["egress_secret_flagged"] is True
    assert rows[0].payload["egress_confirmed"] is True
    # Category NAMES only — never the matched value (§5.5 / Risk 7).
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(rows[0].payload)  # gitleaks:allow


async def test_local_only_secret_sends_without_friction(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secret on a local_only engagement POSTs with no friction and routes to local."""
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["local"]))
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _boom_stream())

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id)  # local_only

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content=_SECRET
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    _ = [c async for c in service.stream_assistant_reply(message=message)]

    rows = await _ai_call_rows(pg_factory, user_id)
    assert len(rows) == 1
    assert rows[0].payload["backend"] == "local"
    assert rows[0].payload["egress_secret_flagged"] is False


async def test_cloud_without_key_marks_failed_no_fallback(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_enabled + no key: the WS turn fails; the local client is never called (§5.1)."""
    monkeypatch.delenv("ADEPTUS_ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom_stream())
    monkeypatch.setattr(service.anthropic_client, "stream_chat", _boom_stream())

    user = await _seed_user(pg_factory, "owner")
    user_id = cast(uuid.UUID, user.id)
    eng_id = await _seed_engagement(pg_factory, user_id, cloud=True)

    async with pg_factory() as db:
        result = await service.send_message(
            db, engagement_id=eng_id, requester=user, content="ordinary question"
        )
        await db.commit()
    assistant_id = result.assistant_message.id

    async with pg_factory() as db:
        message = await chat_repo.get_message_for_owner(
            db, message_id=assistant_id, user_id=user_id
        )
    assert message is not None
    chunks = [c async for c in service.stream_assistant_reply(message=message)]
    assert [c.type for c in chunks] == ["error"]
    assert chunks[0].message == service.CLOUD_NOT_CONFIGURED_MESSAGE

    async with pg_factory() as db:
        row = await chat_repo.get_message_for_owner(db, message_id=assistant_id, user_id=user_id)
        assert row is not None
        assert row.status == "failed"

    rows = await _ai_call_rows(pg_factory, user_id)
    assert len(rows) == 1
    assert rows[0].payload["status"] == "failed"
    assert rows[0].payload["backend"] == "cloud"
