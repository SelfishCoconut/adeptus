"""Tests for auth Pydantic v2 schemas."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.features.auth.schemas import LoginRequest, UserMe


def test_login_request_accepts_valid_input() -> None:
    req = LoginRequest(username="alice", password="hunter2")
    assert req.username == "alice"
    assert req.password == "hunter2"


def test_login_request_rejects_missing_username() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(password="hunter2")  # type: ignore[call-arg]


def test_login_request_rejects_missing_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(username="alice")  # type: ignore[call-arg]


def test_user_me_round_trip() -> None:
    user_id = uuid.uuid4()

    me_none = UserMe(
        id=user_id,
        username="alice",
        role="user",
        terms_accepted_at=None,
    )
    assert me_none.id == user_id
    assert me_none.username == "alice"
    assert me_none.role == "user"
    assert me_none.terms_accepted_at is None

    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    me_with_ts = UserMe.model_validate(
        {"id": user_id, "username": "alice", "role": "admin", "terms_accepted_at": now}
    )
    assert me_with_ts.terms_accepted_at == now

    dumped = me_with_ts.model_dump_json()
    assert "2026-01-15" in dumped


def test_user_me_role_enum_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        UserMe.model_validate(
            {"id": uuid.uuid4(), "username": "alice", "role": "root", "terms_accepted_at": None}
        )


def test_user_me_from_attributes() -> None:
    user_id = uuid.uuid4()
    obj = SimpleNamespace(
        id=user_id,
        username="bob",
        role="admin",
        terms_accepted_at=None,
    )
    me = UserMe.model_validate(obj)
    assert me.id == user_id
    assert me.username == "bob"
    assert me.role == "admin"
    assert me.terms_accepted_at is None
