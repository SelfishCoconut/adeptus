"""Feature-local FastAPI dependencies: current-user resolution."""

from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AuthenticationError
from app.features.auth import repository as repo
from app.features.auth.models import Session, User


async def get_current_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Resolve the request's session cookie to a Session. Raises AuthenticationError if the
    cookie is missing, the session row doesn't exist, or the session has expired.

    All failure paths return the same generic message: distinguishing "no cookie" from
    "unknown session" from "expired" would hand an attacker a session-state oracle.

    The cookie name is read from settings (not a hardcoded literal) so the reader can
    never silently drift from the writer in router.py if SESSION_COOKIE_NAME changes.
    """
    session_id = request.cookies.get(get_settings().SESSION_COOKIE_NAME)
    if session_id is None:
        raise AuthenticationError("Not authenticated")

    session = await repo.get_session(db, session_id)
    if session is None:
        raise AuthenticationError("Not authenticated")

    now = datetime.now(UTC)
    exp = session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp <= now:
        raise AuthenticationError("Not authenticated")

    return session


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    session: Annotated[Session, Depends(get_current_session)],
) -> User:
    """Resolve the current session to a User. Raises AuthenticationError if user not found."""
    user = await repo.get_user_by_id(db, cast(UUID, session.user_id))
    if user is None:
        raise AuthenticationError("Not authenticated")
    return user
