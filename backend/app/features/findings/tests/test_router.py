"""Router tests for the findings feature (Slice 19 task 7).

Happy-path tests wire an ``AsyncClient`` to a real FastAPI app backed by an
in-memory SQLite test DB, so the full router → service → repository → audit path
executes in-process (mirrors the graph router test harness). Error cases that
need awkward DB state (non-member, archived) are driven against real rows; 401
needs no login and 422 is pure Pydantic validation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo
from app.features.findings import models as findings_models  # noqa: F401 — registers findings
from app.features.findings.router import router as findings_router
from app.features.graph import models as graph_models  # noqa: F401 — registers graph_nodes
from app.features.graph.models import GraphNode

_hasher = PasswordHasher()


def _apply_sqlite_patches() -> None:
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)
    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()
    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)
    node_id_col: Column = graph_models.GraphNode.__table__.c.id  # type: ignore[assignment]
    node_id_col.default = ColumnDefault(uuid4)
    finding_id_col: Column = findings_models.Finding.__table__.c.id  # type: ignore[assignment]
    finding_id_col.default = ColumnDefault(uuid4)
    hist_id_col: Column = findings_models.FindingHistory.__table__.c.id  # type: ignore[assignment]
    hist_id_col.default = ColumnDefault(uuid4)


async def _build_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    _apply_sqlite_patches()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _make_app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(findings_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest_asyncio.fixture
async def env(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    engine, factory = await _build_engine_and_factory()
    app = _make_app(factory)
    yield app, factory
    get_settings.cache_clear()
    await engine.dispose()


async def _login(
    factory: async_sessionmaker[AsyncSession], app: FastAPI
) -> tuple[AsyncClient, UUID]:
    """Create a user + an owned engagement, log in, return (client, engagement_id)."""
    async with factory() as session:
        user = await auth_repo.create_user(
            session, username="member", password_hash=_hasher.hash("testpass"), role="user"
        )
        await session.commit()
        await session.refresh(user)
        user_id: UUID = user.id  # type: ignore[assignment]
    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session, name="E", scope="s", client_info=None, owner_id=user_id
        )
        await session.commit()
        await session.refresh(engagement)
        engagement_id: UUID = engagement.id  # type: ignore[assignment]
    client = AsyncClient(transport=ASGITransport(app=app), base_url="https://test")
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "member", "password": "testpass"}
    )
    assert resp.status_code == 200, resp.text
    return client, engagement_id


@pytest_asyncio.fixture
async def member(
    env: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> AsyncGenerator[tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]], None]:
    app, factory = env
    client, engagement_id = await _login(factory, app)
    try:
        yield client, engagement_id, factory
    finally:
        await client.aclose()


def _base(engagement_id: UUID) -> str:
    return f"/api/v1/engagements/{engagement_id}/findings"


async def _create(client: AsyncClient, eng: UUID, **overrides: object) -> dict[str, Any]:
    body = {"title": "Reflected XSS on /search", "severity": "high"}
    body.update(overrides)  # type: ignore[arg-type]
    resp = await client.post(_base(eng), json=body)
    return cast("dict[str, Any]", resp.json())


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_create_finding_201(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    resp = await client.post(
        _base(eng), json={"title": "SQLi on /login", "severity": "critical", "description": "boom"}
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["severity"] == "critical"
    assert data["verification_status"] == "unverified"
    assert data["remediation_status"] == "open"
    assert data["node_id"] is None
    assert data["deleted"] is False


async def test_list_findings_200(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    await _create(client, eng, title="one")
    await _create(client, eng, title="two")
    resp = await client.get(_base(eng))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    # Both findings are returned. (Deterministic newest-first ordering is verified
    # in the repository test with explicit created_at — SQLite's second-resolution
    # CURRENT_TIMESTAMP ties two same-second inserts, so ordering is not asserted here.)
    assert {item["title"] for item in items} == {"one", "two"}


async def test_get_finding_200(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.get(f"{_base(eng)}/{created['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == created["id"]


async def test_update_finding_200(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.patch(
        f"{_base(eng)}/{created['id']}",
        json={"title": "Updated", "severity": "low", "description": "new details"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "Updated"
    assert data["severity"] == "low"
    assert data["description"] == "new details"


async def test_update_finding_links_and_unlinks_node(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, factory = member
    # Insert a live node in this engagement.
    async with factory() as session:
        node = GraphNode(engagement_id=eng, type="host", label="10.0.0.5", properties={})
        session.add(node)
        await session.commit()
        await session.refresh(node)
        node_id = str(node.id)
    created = await _create(client, eng)
    # Link.
    resp = await client.patch(f"{_base(eng)}/{created['id']}", json={"node_id": node_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_id"] == node_id
    # Unlink (explicit null).
    resp = await client.patch(f"{_base(eng)}/{created['id']}", json={"node_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_id"] is None


async def test_create_unknown_node_404(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    resp = await client.post(
        _base(eng), json={"title": "x", "severity": "low", "node_id": str(uuid4())}
    )
    assert resp.status_code == 404, resp.text


async def test_set_verification_200(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.patch(
        f"{_base(eng)}/{created['id']}/verification", json={"verification_status": "false_positive"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_status"] == "false_positive"


async def test_set_remediation_200(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.patch(
        f"{_base(eng)}/{created['id']}/remediation", json={"remediation_status": "risk_accepted"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["remediation_status"] == "risk_accepted"


async def test_delete_finding_204_and_hidden(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.delete(f"{_base(eng)}/{created['id']}")
    assert resp.status_code == 204, resp.text
    # Hidden from the default list.
    items = (await client.get(_base(eng))).json()["items"]
    assert items == []
    # Visible with include_deleted.
    items = (await client.get(_base(eng), params={"include_deleted": "true"})).json()["items"]
    assert len(items) == 1
    assert items[0]["deleted"] is True


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_create_unauthenticated_401(
    env: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(_base(uuid4()), json={"title": "x", "severity": "low"})
        assert resp.status_code == 401, resp.text


async def test_create_non_member_404(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, _, _ = member
    # A random engagement the user is not a member of → 404 (no existence disclosure).
    resp = await client.post(_base(uuid4()), json={"title": "x", "severity": "low"})
    assert resp.status_code == 404, resp.text


async def test_create_archived_409(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, factory = member
    async with factory() as session:
        await session.execute(
            sa_update(eng_models.Engagement)
            .where(eng_models.Engagement.id == eng)
            .values(status="archived")
        )
        await session.commit()
    resp = await client.post(_base(eng), json={"title": "x", "severity": "low"})
    assert resp.status_code == 409, resp.text
    # Reads still work on an archived engagement.
    assert (await client.get(_base(eng))).status_code == 200


async def test_create_bad_severity_422(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    resp = await client.post(_base(eng), json={"title": "x", "severity": "sev9000"})
    assert resp.status_code == 422, resp.text


async def test_create_empty_title_422(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    resp = await client.post(_base(eng), json={"title": "", "severity": "low"})
    assert resp.status_code == 422, resp.text


async def test_verification_bad_value_422(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.patch(
        f"{_base(eng)}/{created['id']}/verification", json={"verification_status": "maybe"}
    )
    assert resp.status_code == 422, resp.text


async def test_update_empty_body_422(
    member: tuple[AsyncClient, UUID, async_sessionmaker[AsyncSession]],
) -> None:
    client, eng, _ = member
    created = await _create(client, eng)
    resp = await client.patch(f"{_base(eng)}/{created['id']}", json={})
    assert resp.status_code == 422, resp.text
