import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_settings_reads_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$hash")

    settings = get_settings()

    assert settings.DATABASE_URL == "postgresql+asyncpg://user:pass@localhost/testdb"
    assert settings.ADEPTUS_ADMIN_USER == "admin"
    assert settings.ADEPTUS_ADMIN_PASSWORD_HASH == "$argon2id$v=19$m=65536,t=3,p=4$hash"


def test_settings_defaults_for_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$hash")

    settings = get_settings()

    assert settings.SESSION_COOKIE_NAME == "session_id"
    assert settings.SESSION_TTL_DAYS == 14
    assert settings.ENVIRONMENT == "production"


def test_settings_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADEPTUS_ADMIN_USER", raising=False)
    monkeypatch.delenv("ADEPTUS_ADMIN_PASSWORD_HASH", raising=False)

    with pytest.raises(ValidationError):
        Settings()
