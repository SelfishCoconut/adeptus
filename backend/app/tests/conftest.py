"""Conftest for app-level tests.

Sets the three required env vars and clears the lru_cache on get_settings so that
any test that indirectly triggers settings validation has a valid environment.
"""

import os

import pytest

# Set env vars at module load time (before any app module is imported during collection).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("ADEPTUS_ADMIN_USER", "admin")
os.environ.setdefault(
    "ADEPTUS_ADMIN_PASSWORD_HASH",
    "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Clear get_settings lru_cache before each test to prevent cross-test pollution."""
    from app.core.config import get_settings
    from app.core.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
