"""Single source of truth for the session cookie's attributes.

Both the writer (router login / the sliding-expiry refresh in deps) and the clear
path use these helpers so the set and clear cannot drift apart — a mismatch in
attributes can leave a cookie the browser refuses to delete. Attributes are
``HttpOnly; Secure; SameSite=Lax; Path=/`` per ADR-0003 / ADR-0007.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import Response

from app.core.config import get_settings

_COOKIE_HTTPONLY = True
_COOKIE_SECURE = True
_COOKIE_SAMESITE: Literal["lax"] = "lax"
_COOKIE_PATH = "/"


def set_session_cookie(response: Response, session_id: str, expires_at: datetime) -> None:
    """Set the session cookie with its security attributes.

    Use max_age (seconds until expiry) rather than passing an absolute epoch to the
    ``expires`` int param: Starlette interprets an int ``expires`` as seconds-from-now, so
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


def clear_session_cookie(response: Response) -> None:
    """Clear the session cookie using the same attributes it was set with."""
    response.delete_cookie(
        key=get_settings().SESSION_COOKIE_NAME,
        httponly=_COOKIE_HTTPONLY,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        path=_COOKIE_PATH,
    )
