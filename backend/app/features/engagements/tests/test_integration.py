"""Integration tests for the slice-01 engagements migration and slice-02 privacy_mode.

The first test drives the real Alembic migration ``1eb283db46ca`` up and down
against a *throwaway* Postgres database, asserting the new tables exist, the
``status`` server default and CHECK constraint behave, and the downgrade fully
removes them.

The second test (added in slice-02) exercises the ``privacy_mode`` field via the
real FastAPI app + a throwaway Postgres schema — matching the pattern used by
``app/features/auth/tests/test_integration.py``: async ORM engine scoped to an
isolated schema, httpx AsyncClient over the ASGI transport, no Alembic involved.

A dedicated database — rather than the throwaway-schema trick used by the auth
integration suite — is used by the migration test because Alembic's ``env.py``
builds its own engine from ``DATABASE_URL`` and offers no hook to scope it to a
search_path. Each Alembic ``command`` call runs ``asyncio.run`` internally (env.py
online runner), so that test is deliberately *synchronous*.

Both tests are marked ``integration`` (deselected by the default
``make test-backend`` run) and executed by ``make test-integration``. Both skip
cleanly when no Postgres is reachable. Point them at a server with
``ADEPTUS_TEST_DATABASE_URL``; both default to the compose Postgres on localhost.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from alembic.command import downgrade, upgrade
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models  # noqa: F401 — registers ORM metadata
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — registers ORM metadata
from app.features.engagements.router import router as engagements_router

pytestmark = pytest.mark.integration

_DOWN_REVISION = "0a1f3f9f803c"  # slice-00 users/sessions — the parent revision
_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_ALEMBIC_INI = Path(__file__).parents[4] / "alembic.ini"


def _sqlalchemy_dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


def _asyncpg_dsn(sqlalchemy_dsn: str) -> str:
    """asyncpg.connect wants a bare libpq URL, without the ``+asyncpg`` driver tag."""
    return sqlalchemy_dsn.replace("postgresql+asyncpg://", "postgresql://")


def _with_database(asyncpg_dsn: str, database: str) -> str:
    base, _, _ = asyncpg_dsn.rpartition("/")
    return f"{base}/{database}"


async def _admin_exec(dsn: str, statement: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(statement)
    finally:
        await conn.close()


def _alembic_config(sqlalchemy_dsn: str) -> Config:
    """An Alembic config whose env.py will target ``sqlalchemy_dsn`` via DATABASE_URL."""
    os.environ["DATABASE_URL"] = sqlalchemy_dsn
    # Settings() requires the admin bootstrap vars; they are irrelevant to the
    # migration but must validate. setdefault leaves a real test-integration env
    # (docker / .env) untouched.
    os.environ.setdefault("ADEPTUS_ADMIN_USER", "admin")
    os.environ.setdefault(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    return Config(str(_ALEMBIC_INI))


def test_engagements_migration_upgrade_downgrade_cycle() -> None:
    """Apply the engagements migration to a fresh DB, exercise its constraints,
    then downgrade and confirm the tables are gone."""
    admin_sa_dsn = _sqlalchemy_dsn()
    admin_pg_dsn = _asyncpg_dsn(admin_sa_dsn)
    db_name = f"mig_test_{uuid.uuid4().hex[:12]}"

    # CREATE DATABASE must run outside a transaction; asyncpg autocommits a lone execute.
    try:
        asyncio.run(_admin_exec(admin_pg_dsn, f'CREATE DATABASE "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 — any connect failure means "no PG here"
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    target_sa_dsn = _with_database(admin_sa_dsn, db_name)
    target_pg_dsn = _with_database(admin_pg_dsn, db_name)
    original_database_url = os.environ.get("DATABASE_URL")

    try:
        cfg = _alembic_config(target_sa_dsn)

        # Fresh DB -> head applies slice-00 then the slice-01 engagements migration.
        upgrade(cfg, "head")
        asyncio.run(_assert_schema_present_and_enforced(target_pg_dsn))

        # Downgrade one step removes exactly the engagements migration.
        downgrade(cfg, _DOWN_REVISION)
        asyncio.run(_assert_engagements_tables_absent(target_pg_dsn))
    finally:
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()
        # Terminate lingering backends before dropping the throwaway database.
        asyncio.run(
            _admin_exec(
                admin_pg_dsn,
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()",
            )
        )
        asyncio.run(_admin_exec(admin_pg_dsn, f'DROP DATABASE IF EXISTS "{db_name}"'))


async def _assert_schema_present_and_enforced(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert "engagements" in tables
        assert "engagement_members" in tables

        # status server default resolves to 'active'.
        status = await conn.fetchval(
            "INSERT INTO engagements (name, scope) VALUES ($1, $2) RETURNING status",
            "ACME Web Assessment",
            "192.168.1.0/24",
        )
        assert status == "active"

        # CHECK constraint rejects an out-of-domain status.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO engagements (name, scope, status) VALUES ($1, $2, $3)",
                "bad",
                "scope",
                "bogus",
            )

        # engagement_members carries its role CHECK and the user_id index.
        member_constraints = {
            r["constraint_name"]
            for r in await conn.fetch(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = 'engagement_members'"
            )
        }
        assert "ck_engagement_members_role" in member_constraints
        indexes = {
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'engagement_members'"
            )
        }
        assert "ix_engagement_members_user_id" in indexes
    finally:
        await conn.close()


async def _assert_engagements_tables_absent(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert "engagements" not in tables
        assert "engagement_members" not in tables
        # The parent slice-00 tables survive the one-step downgrade.
        assert "users" in tables
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Slice-02: privacy_mode API round-trip integration test
#
# Pattern: throwaway Postgres schema + httpx AsyncClient over ASGI transport.
# Matches app/features/auth/tests/test_integration.py exactly.
# ---------------------------------------------------------------------------

_DEFAULT_API_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_OWNER_PW = "correcthorse"
_OWNER_HASH = PasswordHasher().hash(_OWNER_PW)


def _api_dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_API_DSN


@pytest_asyncio.fixture
async def pg_schema_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema.

    Skips the test if Postgres is unreachable so the suite stays green on
    machines without the compose stack up.
    """
    schema = f"eng_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_api_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001 — any connect/setup failure means "no PG here"
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(
        _api_dsn(),
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


def _make_eng_app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(engagements_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest_asyncio.fixture(autouse=True)
async def _settings_env(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    """Set the env vars required by get_settings() for tests that hit the auth router.

    The migration test sets DATABASE_URL via os.environ directly, but it restores
    the original value in its finally block — so the API round-trip test may run
    with DATABASE_URL unset.  This autouse fixture ensures Settings() always has
    the minimum required vars for both tests in this module.
    """
    monkeypatch.setenv("DATABASE_URL", _api_dsn())
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_USER",
        os.environ.get("ADEPTUS_ADMIN_USER", "admin"),
    )
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        os.environ.get(
            "ADEPTUS_ADMIN_PASSWORD_HASH",
            "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
        ),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_privacy_mode_create_get_patch_round_trip(
    pg_schema_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Create engagement with privacy_mode=cloud_enabled, GET it, PATCH back to local_only.

    Exercises the full stack: router -> service -> repository -> real Postgres.
    """
    # Seed an owner user directly via the repository.
    async with pg_schema_factory() as session:
        await auth_repo.create_user(
            session, username="owner", password_hash=_OWNER_HASH, role="admin"
        )
        await session.commit()

    app = _make_eng_app(pg_schema_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # Log in to obtain a session cookie.
        login = await client.post(
            "/api/v1/auth/login", json={"username": "owner", "password": _OWNER_PW}
        )
        assert login.status_code == 200, login.text

        # POST: create engagement with privacy_mode=cloud_enabled.
        create_resp = await client.post(
            "/api/v1/engagements",
            json={
                "name": "Privacy Round-Trip Test",
                "scope": "192.168.1.0/24",
                "privacy_mode": "cloud_enabled",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created["privacy_mode"] == "cloud_enabled"
        engagement_id = created["id"]

        # GET: retrieve and assert privacy_mode round-trips.
        get_resp = await client.get(f"/api/v1/engagements/{engagement_id}")
        assert get_resp.status_code == 200, get_resp.text
        get_body = get_resp.json()
        assert get_body["privacy_mode"] == "cloud_enabled"

        # Capture updated_at before the PATCH to verify it advances (W-05).
        updated_at_before = get_body["updated_at"]

        # PATCH: flip back to local_only and assert the updated response.
        patch_resp = await client.patch(
            f"/api/v1/engagements/{engagement_id}",
            json={"privacy_mode": "local_only"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        patch_body = patch_resp.json()
        assert patch_body["privacy_mode"] == "local_only"

        # W-05 verification: updated_at must strictly increase after the PATCH.
        # SQLAlchemy's onupdate=func.now() on the raw update() statement renders
        # the updated_at into the SET clause — this assertion confirms it works.
        updated_at_after = patch_body["updated_at"]
        assert updated_at_after > updated_at_before, (
            f"updated_at did not advance after PATCH: before={updated_at_before!r}, "
            f"after={updated_at_after!r}"
        )
