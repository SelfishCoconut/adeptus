"""Service-layer tests for the auth feature."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from argon2 import PasswordHasher

from app.core.errors import AuthenticationError, ValidationError
from app.features.auth import service
from app.features.auth.models import User


def _make_user(
    *,
    password_hash: str = "",
    terms_accepted_at: object = None,
) -> User:
    """Build a minimal User ORM object without a DB session."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.username = "testuser"
    user.password_hash = password_hash
    user.role = "user"
    user.terms_accepted_at = terms_accepted_at
    return user


# ---------------------------------------------------------------------------
# authenticate_user
# ---------------------------------------------------------------------------


async def test_authenticate_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: repo returns a User with a matching hash → returns the User."""
    ph = PasswordHasher()
    pw = "correct-horse-battery-staple"
    user = _make_user(password_hash=ph.hash(pw))

    mock_get = AsyncMock(return_value=user)
    monkeypatch.setattr(service.repo, "get_user_by_username", mock_get)

    db = AsyncMock()
    result = await service.authenticate_user(db, username="testuser", password=pw)

    assert result is user
    mock_get.assert_awaited_once_with(db, "testuser")


async def test_authenticate_user_wrong_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repo returns a User but password is wrong → AuthenticationError."""
    ph = PasswordHasher()
    user = _make_user(password_hash=ph.hash("right-password"))

    monkeypatch.setattr(service.repo, "get_user_by_username", AsyncMock(return_value=user))

    db = AsyncMock()
    with pytest.raises(AuthenticationError):
        await service.authenticate_user(db, username="testuser", password="wrong-password")


async def test_authenticate_user_unknown_username(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repo returns None (unknown user) → AuthenticationError.

    Also verifies the dummy-verify timing branch completes without crashing.
    """
    monkeypatch.setattr(service.repo, "get_user_by_username", AsyncMock(return_value=None))

    db = AsyncMock()
    with pytest.raises(AuthenticationError):
        await service.authenticate_user(db, username="ghost", password="any-password")


# ---------------------------------------------------------------------------
# bootstrap_admin
# ---------------------------------------------------------------------------


def _make_settings(*, pw_hash: str = "$argon2id$v=19$m=65536,t=3,p=4$abc$def") -> SimpleNamespace:
    return SimpleNamespace(
        ADEPTUS_ADMIN_USER="admin",
        ADEPTUS_ADMIN_PASSWORD_HASH=pw_hash,
    )


async def test_bootstrap_admin_creates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """No existing admin user → create_admin_if_absent called once, returns the new User."""
    ph = PasswordHasher()
    valid_hash = ph.hash("irrelevant-at-bootstrap")
    settings = _make_settings(pw_hash=valid_hash)

    monkeypatch.setattr(service, "get_settings", lambda: settings)
    created_user = _make_user(password_hash=valid_hash)
    mock_create = AsyncMock(return_value=created_user)
    monkeypatch.setattr(service.repo, "create_admin_if_absent", mock_create)

    db = AsyncMock()
    result = await service.bootstrap_admin(db)

    assert result is created_user
    mock_create.assert_awaited_once_with(
        db,
        username="admin",
        password_hash=valid_hash,
    )


async def test_bootstrap_admin_noop_when_users_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin username already present → create_admin_if_absent returns None (no-op)."""
    settings = _make_settings()

    monkeypatch.setattr(service, "get_settings", lambda: settings)
    mock_create = AsyncMock(return_value=None)
    monkeypatch.setattr(service.repo, "create_admin_if_absent", mock_create)

    db = AsyncMock()
    result = await service.bootstrap_admin(db)

    assert result is None
    mock_create.assert_awaited_once()


async def test_bootstrap_admin_rejects_plaintext_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings hash is plaintext → ValidationError raised before any insert is attempted."""
    settings = _make_settings(pw_hash="plaintext-not-argon2")

    monkeypatch.setattr(service, "get_settings", lambda: settings)
    mock_create = AsyncMock()
    monkeypatch.setattr(service.repo, "create_admin_if_absent", mock_create)

    db = AsyncMock()
    with pytest.raises(ValidationError):
        await service.bootstrap_admin(db)

    mock_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# accept_terms
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# bootstrap_test_user
# ---------------------------------------------------------------------------


def _make_test_settings(
    *,
    environment: str = "development",
    username: str | None = "tester",
    password: str | None = "changeme",
) -> SimpleNamespace:
    return SimpleNamespace(
        ENVIRONMENT=environment,
        ADEPTUS_TEST_USER_USERNAME=username,
        ADEPTUS_TEST_USER_PASSWORD=password,
        ADEPTUS_ADMIN_USER="admin",
        ADEPTUS_ADMIN_PASSWORD_HASH="$argon2id$v=19$m=65536,t=3,p=4$abc$def",
    )


async def test_test_user_seeder_skipped_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENVIRONMENT=production → bootstrap_test_user returns None without creating any user.

    The env guard must fire even when username and password are both set, proving it is
    the ENVIRONMENT check (not the missing-var guard) that blocks the seed path.
    """
    settings = _make_test_settings(environment="production")
    monkeypatch.setattr(service, "get_settings", lambda: settings)

    mock_create = AsyncMock()
    monkeypatch.setattr(service.repo, "create_user", mock_create)
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr(service.repo, "get_user_by_username", mock_get)

    db = AsyncMock()
    result = await service.bootstrap_test_user(db)

    assert result is None
    mock_create.assert_not_awaited()
    mock_get.assert_not_awaited()


async def test_test_user_seeder_creates_user_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENVIRONMENT=development + both vars set + user absent → create_user called once."""
    settings = _make_test_settings(environment="development")
    monkeypatch.setattr(service, "get_settings", lambda: settings)

    created_user = _make_user()
    mock_create = AsyncMock(return_value=created_user)
    monkeypatch.setattr(service.repo, "create_user", mock_create)
    monkeypatch.setattr(service.repo, "get_user_by_username", AsyncMock(return_value=None))

    db = AsyncMock()
    result = await service.bootstrap_test_user(db)

    assert result is created_user
    mock_create.assert_awaited_once()
    # Verify role is "user" (not "admin")
    _, kwargs = mock_create.call_args
    assert kwargs["role"] == "user"
    assert kwargs["username"] == "tester"


async def test_test_user_seeder_noop_when_user_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User already exists → returns None without calling create_user (idempotent)."""
    settings = _make_test_settings(environment="test")
    monkeypatch.setattr(service, "get_settings", lambda: settings)

    existing_user = _make_user()
    mock_get = AsyncMock(return_value=existing_user)
    monkeypatch.setattr(service.repo, "get_user_by_username", mock_get)
    mock_create = AsyncMock()
    monkeypatch.setattr(service.repo, "create_user", mock_create)

    db = AsyncMock()
    result = await service.bootstrap_test_user(db)

    assert result is None
    mock_create.assert_not_awaited()


async def test_test_user_seeder_noop_when_vars_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """USERNAME or PASSWORD env var absent → returns None without any DB call."""
    settings = _make_test_settings(environment="development", username=None, password=None)
    monkeypatch.setattr(service, "get_settings", lambda: settings)

    mock_create = AsyncMock()
    monkeypatch.setattr(service.repo, "create_user", mock_create)
    mock_get = AsyncMock()
    monkeypatch.setattr(service.repo, "get_user_by_username", mock_get)

    db = AsyncMock()
    result = await service.bootstrap_test_user(db)

    assert result is None
    mock_create.assert_not_awaited()
    mock_get.assert_not_awaited()


# ---------------------------------------------------------------------------
# accept_terms
# ---------------------------------------------------------------------------


async def test_accept_terms_sets_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """repo.update_terms_accepted called and its return value propagated."""
    import datetime

    updated_user = _make_user(terms_accepted_at=datetime.datetime.now(datetime.UTC))
    mock_update = AsyncMock(return_value=updated_user)
    monkeypatch.setattr(service.repo, "update_terms_accepted", mock_update)

    db = AsyncMock()
    user_id = uuid4()
    result = await service.accept_terms(db, user_id=user_id)

    assert result is updated_user
    mock_update.assert_awaited_once_with(db, user_id)


async def test_accept_terms_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling accept_terms twice does not error; repo called twice (idempotent overwrite)."""
    import datetime

    updated_user = _make_user(terms_accepted_at=datetime.datetime.now(datetime.UTC))
    mock_update = AsyncMock(return_value=updated_user)
    monkeypatch.setattr(service.repo, "update_terms_accepted", mock_update)

    db = AsyncMock()
    user_id = uuid4()

    # First call
    result1 = await service.accept_terms(db, user_id=user_id)
    # Second call — must not raise
    result2 = await service.accept_terms(db, user_id=user_id)

    assert result1 is updated_user
    assert result2 is updated_user
    assert mock_update.await_count == 2
