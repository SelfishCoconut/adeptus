"""Async DB access for users and sessions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.errors import NotFoundError
from app.features.auth.models import Session, User


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Return the User with the given username, or None."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    """Return the User with the given id, or None."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password_hash: str,
    role: str = "user",
) -> User:
    """Insert a new User row and flush so the server-generated id is populated."""
    user = User(username=username, password_hash=password_hash, role=role)
    db.add(user)
    await db.flush()
    return user


async def create_admin_if_absent(
    db: AsyncSession,
    *,
    username: str,
    password_hash: str,
) -> User | None:
    """Atomically create the admin user, or no-op if the username already exists.

    Uses INSERT ... ON CONFLICT DO NOTHING so two backend processes starting
    concurrently cannot race into a duplicate-insert error (slice-00 security
    gate / ADR-0003). Returns the newly-created User, or None if it already
    existed. The conflict target is the unique ix_users_username index.
    """
    stmt = (
        pg_insert(User)
        .values(username=username, password_hash=password_hash, role="admin")
        .on_conflict_do_nothing(index_elements=["username"])
    )
    result = await db.execute(stmt)
    if not result.rowcount:  # type: ignore[attr-defined]
        return None
    return await get_user_by_username(db, username)


async def update_terms_accepted(db: AsyncSession, user_id: UUID) -> User:
    """Set terms_accepted_at to now() for the given user. Raises NotFoundError if missing."""
    stmt = (
        update(User).where(User.id == user_id).values(terms_accepted_at=func.now()).returning(User)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def create_session(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: UUID,
    expires_at: datetime,
    user_agent: str | None = None,
    ip: str | None = None,
) -> Session:
    """Insert a new Session row and flush so it is immediately visible."""
    session = Session(
        id=session_id,
        user_id=user_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    db.add(session)
    await db.flush()
    return session


async def get_session(db: AsyncSession, session_id: str) -> Session | None:
    """Return the Session with the given id, or None."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


async def refresh_session(
    db: AsyncSession,
    session_id: str,
    *,
    new_expires_at: datetime,
) -> Session | None:
    """Update last_used_at to now() and expires_at to new_expires_at. Returns the row or None."""
    stmt = (
        update(Session)
        .where(Session.id == session_id)
        .values(last_used_at=func.now(), expires_at=new_expires_at)
        .returning(Session)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def delete_session(db: AsyncSession, session_id: str) -> bool:
    """Delete the session row. Returns True if a row was deleted, False otherwise."""
    stmt = delete(Session).where(Session.id == session_id)
    result = await db.execute(stmt)
    return result.rowcount > 0  # type: ignore[attr-defined, no-any-return]


async def delete_expired_sessions(db: AsyncSession, *, now: datetime) -> int:
    """Delete all sessions whose expires_at is before now. Returns the count deleted."""
    stmt = delete(Session).where(Session.expires_at < now)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
