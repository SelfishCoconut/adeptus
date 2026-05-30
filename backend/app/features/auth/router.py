"""FastAPI routes for authentication: login, logout, me, accept-terms."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.features.auth import repository as repo
from app.features.auth import service
from app.features.auth.deps import get_current_session, get_current_user
from app.features.auth.models import Session, User
from app.features.auth.schemas import LoginRequest, UserMe

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Single source of truth for the session cookie's security attributes so that the
# set and clear paths can never drift apart (a mismatch can leave a cookie the
# browser won't delete). HttpOnly + Secure + SameSite=Lax per ADR-0003/ADR-0007.
_COOKIE_HTTPONLY = True
_COOKIE_SECURE = True
_COOKIE_SAMESITE: Literal["lax"] = "lax"
_COOKIE_PATH = "/"


def _set_session_cookie(response: Response, session_id: str, expires_at: datetime) -> None:
    """Set the session cookie with security attributes.

    Use max_age (seconds until expiry) rather than passing an absolute epoch to the
    `expires` int param: Starlette interprets an int `expires` as seconds-from-now, so
    an absolute timestamp would yield a decades-long cookie lifetime.
    """
    max_age = int((expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        key=get_settings().SESSION_COOKIE_NAME,
        value=session_id,
        httponly=_COOKIE_HTTPONLY,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        max_age=max_age,
        path=_COOKIE_PATH,
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear the session cookie using the same attributes it was set with."""
    response.delete_cookie(
        key=get_settings().SESSION_COOKIE_NAME,
        httponly=_COOKIE_HTTPONLY,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        path=_COOKIE_PATH,
    )


@router.post("/login", response_model=UserMe)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserMe:
    """Authenticate with username + password; create a server-side session and set the cookie."""
    user = await service.authenticate_user(db, username=body.username, password=body.password)

    settings = get_settings()
    session_id = secrets.token_hex(32)  # 256-bit opaque token
    expires_at = datetime.now(UTC) + timedelta(days=settings.SESSION_TTL_DAYS)

    await repo.create_session(
        db,
        session_id=session_id,
        user_id=user.id,  # type: ignore[arg-type]
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    await db.commit()

    _set_session_cookie(response, session_id, expires_at)
    return UserMe.model_validate(user)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    session: Annotated[Session, Depends(get_current_session)],
) -> None:
    """Delete the server-side session and clear the cookie."""
    await repo.delete_session(db, session.id)
    await db.commit()
    _clear_session_cookie(response)


@router.get("/me", response_model=UserMe)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserMe:
    """Return the currently authenticated user."""
    return UserMe.model_validate(current_user)


@router.post("/accept-terms", response_model=UserMe)
async def accept_terms(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserMe:
    """Record terms-of-use acceptance for the current user."""
    user = await service.accept_terms(db, user_id=current_user.id)  # type: ignore[arg-type]
    await db.commit()
    return UserMe.model_validate(user)
