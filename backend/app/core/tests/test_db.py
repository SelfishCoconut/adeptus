import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db, get_engine, get_sessionmaker


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def test_get_engine_uses_settings_url(monkeypatch: pytest.MonkeyPatch) -> None:
    sqlite_url = "sqlite+aiosqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", sqlite_url)
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$hash")

    engine = get_engine()

    assert engine.url.render_as_string(hide_password=False) == sqlite_url


async def test_get_db_yields_async_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$hash")

    gen = get_db()
    session = await gen.__anext__()

    assert isinstance(session, AsyncSession)

    # Drive the generator to completion (finally block closes the session)
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    # After the async-with context exits, the session is no longer in a transaction
    assert not session.in_transaction()
