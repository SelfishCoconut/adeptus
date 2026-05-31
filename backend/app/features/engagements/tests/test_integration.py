"""Integration tests for the slice-01 engagements migration.

Drives the real Alembic migration ``1eb283db46ca`` up and down against a
*throwaway* Postgres database (created and dropped per test), asserting the
new tables exist, the ``status`` server default and CHECK constraint behave,
and the downgrade fully removes them.

A dedicated database — rather than the throwaway-schema trick used by the auth
integration suite — is used because Alembic's ``env.py`` builds its own engine
from ``DATABASE_URL`` and offers no hook to scope it to a search_path. Each
Alembic ``command`` call runs ``asyncio.run`` internally (env.py online runner),
so this test is deliberately *synchronous*: it sequences its own
``asyncio.run`` helpers for the asyncpg admin work around the sync command API.

Marked ``integration`` (deselected by the default ``make test-backend`` run) and
executed by ``make test-integration``. Skips cleanly when no Postgres is
reachable. Point it at a server with ``ADEPTUS_TEST_DATABASE_URL``; it defaults
to the compose Postgres on localhost.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config

from app.core.config import get_settings

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
