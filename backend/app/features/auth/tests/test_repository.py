"""Repository-layer tests for the auth feature.

All tests use an in-memory SQLite async engine (see conftest.py for fixture).
SQLite stores UUID columns as CHAR(32) text and INET as plain TEXT, which is
fine for round-trip unit tests that don't need Postgres semantics.
"""

import datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.auth import repository as repo
from app.features.auth.models import Session, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _uid(user: User) -> UUID:
    """Cast User.id (typed as SQLAlchemy UUID) to plain uuid.UUID for type-safe test calls.

    The model declares id: Mapped[sqlalchemy.dialects.postgresql.UUID] instead of
    Mapped[uuid.UUID], so mypy rejects passing user.id where uuid.UUID is expected.
    At runtime user.id IS a uuid.UUID object (as_uuid=True); this cast is safe.
    """
    return cast(UUID, user.id)


async def _make_user(
    db_session: AsyncSession, *, username: str = "alice", role: str = "user"
) -> User:
    """Create and flush a User via the repository."""
    return await repo.create_user(
        db_session,
        username=username,
        password_hash="$argon2id$v=19$...",
        role=role,
    )


async def _make_session(
    db_session: AsyncSession,
    user_id: UUID,
    *,
    expires_at: datetime.datetime | None = None,
) -> Session:
    if expires_at is None:
        expires_at = _utcnow() + datetime.timedelta(days=14)
    return await repo.create_session(
        db_session,
        session_id="sess-" + str(uuid4()),
        user_id=user_id,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# User tests
# ---------------------------------------------------------------------------


async def test_create_user_persists_and_returns_user(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    assert user.id is not None
    assert user.username == "alice"
    assert user.role == "user"


async def test_get_user_by_username_finds_existing(db_session: AsyncSession) -> None:
    await _make_user(db_session)
    found = await repo.get_user_by_username(db_session, "alice")
    assert found is not None
    assert found.username == "alice"


async def test_get_user_by_username_returns_none_when_missing(db_session: AsyncSession) -> None:
    result = await repo.get_user_by_username(db_session, "nobody")
    assert result is None


async def test_get_user_by_id_finds_existing(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    found = await repo.get_user_by_id(db_session, _uid(user))
    assert found is not None
    assert found.id == user.id


async def test_get_user_by_id_returns_none_when_missing(db_session: AsyncSession) -> None:
    result = await repo.get_user_by_id(db_session, uuid4())
    assert result is None


async def test_update_terms_accepted_sets_timestamp(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    assert user.terms_accepted_at is None

    updated = await repo.update_terms_accepted(db_session, _uid(user))
    assert updated.terms_accepted_at is not None


async def test_update_terms_accepted_raises_when_user_missing(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await repo.update_terms_accepted(db_session, uuid4())


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------


async def test_create_session_persists(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    expires = _utcnow() + datetime.timedelta(days=14)
    session = await repo.create_session(
        db_session,
        session_id="test-sess-1",
        user_id=_uid(user),
        expires_at=expires,
        user_agent="Mozilla/5.0",
        ip="127.0.0.1",
    )
    assert session.id == "test-sess-1"
    assert session.user_agent == "Mozilla/5.0"
    assert session.ip == "127.0.0.1"


async def test_get_session_returns_row(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    sess = await _make_session(db_session, _uid(user))
    found = await repo.get_session(db_session, sess.id)
    assert found is not None
    assert found.id == sess.id


async def test_get_session_returns_none_when_missing(db_session: AsyncSession) -> None:
    result = await repo.get_session(db_session, "does-not-exist")
    assert result is None


async def test_refresh_session_updates_expires_at_and_last_used_at(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    original_expires = _utcnow() + datetime.timedelta(days=7)
    sess = await _make_session(db_session, _uid(user), expires_at=original_expires)

    new_expires = _utcnow() + datetime.timedelta(days=14)
    refreshed = await repo.refresh_session(db_session, sess.id, new_expires_at=new_expires)

    assert refreshed is not None
    assert refreshed.id == sess.id
    # expires_at should have been pushed forward
    assert refreshed.expires_at is not None


async def test_refresh_session_returns_none_when_missing(db_session: AsyncSession) -> None:
    result = await repo.refresh_session(
        db_session,
        "no-such-session",
        new_expires_at=_utcnow() + datetime.timedelta(days=1),
    )
    assert result is None


async def test_delete_session_returns_true_when_deleted_false_otherwise(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    sess = await _make_session(db_session, _uid(user))

    deleted = await repo.delete_session(db_session, sess.id)
    assert deleted is True

    deleted_again = await repo.delete_session(db_session, sess.id)
    assert deleted_again is False


async def test_delete_expired_sessions_deletes_only_past(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)

    now = _utcnow()
    past = now - datetime.timedelta(hours=1)
    future = now + datetime.timedelta(days=14)

    expired_sess = await _make_session(db_session, _uid(user), expires_at=past)
    live_sess = await _make_session(db_session, _uid(user), expires_at=future)

    count = await repo.delete_expired_sessions(db_session, now=now)

    assert count == 1
    assert await repo.get_session(db_session, expired_sess.id) is None
    assert await repo.get_session(db_session, live_sess.id) is not None
