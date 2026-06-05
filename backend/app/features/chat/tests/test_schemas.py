"""Schema validation tests: content bounds, enum/DB-vocabulary parity."""

import pytest
from pydantic import ValidationError

from app.features.chat import models
from app.features.chat.schemas import (
    MAX_MESSAGE_CHARS,
    ChatMessageCreate,
    ChatMessageStatus,
    ChatRole,
)


def test_role_enum_matches_db_vocabulary() -> None:
    """ChatRole must mirror models.CHAT_ROLES exactly (no silent drift)."""
    assert {r.value for r in ChatRole} == set(models.CHAT_ROLES)


def test_status_enum_matches_db_vocabulary() -> None:
    """ChatMessageStatus must mirror models.CHAT_STATUSES exactly."""
    assert {s.value for s in ChatMessageStatus} == set(models.CHAT_STATUSES)


def test_empty_content_rejected() -> None:
    """A user message must carry at least one character (min_length=1)."""
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="")


def test_content_over_max_rejected() -> None:
    """Content longer than MAX_MESSAGE_CHARS is rejected."""
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="x" * (MAX_MESSAGE_CHARS + 1))


def test_content_at_max_accepted() -> None:
    """Content exactly at the limit is accepted and passed through unchanged (§5.5)."""
    text = "x" * MAX_MESSAGE_CHARS
    assert ChatMessageCreate(content=text).content == text


def test_content_passes_through_verbatim() -> None:
    """No redaction/normalization on the way in (§5.5) — value is byte-for-byte intact,
    even for sensitive-looking content (the model needs full context to be useful)."""
    raw = "  creds for box-7 are <not-redacted-here>\n\ttrailing-whitespace-kept  "
    assert ChatMessageCreate(content=raw).content == raw
