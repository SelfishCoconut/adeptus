"""FastAPI routes for authentication: login, logout, me, accept-terms."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AuthenticationError
from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.auth import repository as repo
from app.features.auth import service
from app.features.auth.cookies import clear_session_cookie, set_session_cookie
from app.features.auth.deps import get_current_session, get_current_user
from app.features.auth.models import Session, User
from app.features.auth.schemas import LoginRequest, UserMe

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=UserMe)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserMe:
    """Authenticate with username + password; create a server-side session and set the cookie."""
    try:
        user = await service.authenticate_user(db, username=body.username, password=body.password)
    except AuthenticationError:
        # §14: audit the failed attempt (no actor; attempted username in the payload),
        # commit it in its own transaction, then re-raise so the handler returns 401.
        await audit_service.record(
            db, action=AuditAction.LOGIN_FAILED, payload={"username": body.username}
        )
        await db.commit()
        raise

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
    # Audit the successful login atomically with the session row (Decision 1).
    await audit_service.record(
        db,
        action=AuditAction.LOGIN,
        actor_user_id=user.id,  # type: ignore[arg-type]
    )
    await db.commit()

    set_session_cookie(response, session_id, expires_at)
    return UserMe.model_validate(user)


@router.post(
    "/logout",
    status_code=204,
    responses={401: {"description": "Not authenticated"}},
)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    session: Annotated[Session, Depends(get_current_session)],
) -> None:
    """Delete the server-side session and clear the cookie."""
    await repo.delete_session(db, session.id)
    # Audit the logout atomically with the session deletion (Decision 1).
    await audit_service.record(
        db,
        action=AuditAction.LOGOUT,
        actor_user_id=session.user_id,  # type: ignore[arg-type]
    )
    await db.commit()
    clear_session_cookie(response)


@router.get(
    "/me",
    response_model=UserMe,
    responses={401: {"description": "Not authenticated"}},
)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserMe:
    """Return the currently authenticated user."""
    return UserMe.model_validate(current_user)


@router.post(
    "/accept-terms",
    response_model=UserMe,
    responses={401: {"description": "Not authenticated"}},
)
async def accept_terms(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserMe:
    """Record terms-of-use acceptance for the current user."""
    user = await service.accept_terms(db, user_id=current_user.id)  # type: ignore[arg-type]
    await db.commit()
    return UserMe.model_validate(user)
