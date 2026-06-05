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
from app.features.audit.models import AuditChainHead, AuditEntry
from app.features.auth import repository as auth_repo
from app.features.auth.models import Session, User
from app.features.chat import repository as chat_repo
from app.features.chat import service
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement, EngagementMember

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_hasher = PasswordHasher()

# Exactly the tables these tests touch (auth + engagements + chat + audit).
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
    factory: async_sessionmaker[AsyncSession], owner_id: uuid.UUID
) -> uuid.UUID:
    async with factory() as db:
        engagement = await eng_repo.create_engagement(
            db, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
        )
        await db.commit()
        await db.refresh(engagement)
        return cast(uuid.UUID, engagement.id)


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
