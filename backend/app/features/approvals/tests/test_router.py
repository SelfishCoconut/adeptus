"""Router tests for the approvals feature (Slice 16 task 6).

httpx.AsyncClient against a full app on SQLite; auth via a real seeded session cookie;
``execute_tool_run`` mocked (no subprocess); the real ``audit_service.record`` runs against
SQLite. Asserts the HTTP translation: 200/404/401 and the two inline 409s.
"""

from __future__ import annotations

import datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.approvals import repository as repo
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.engagements import repository as eng_repo

_hasher = PasswordHasher()
_SESSION_COOKIE = "session_id"

AppFactory = tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncMock]


def _future() -> datetime.datetime:
    return datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)


async def _seed_user(factory: async_sessionmaker[AsyncSession], username: str) -> User:
    async with factory() as s:
        user = await auth_repo.create_user(
            s, username=username, password_hash=_hasher.hash("pw"), role="user"
        )
        await s.commit()
        await s.refresh(user)
        return user


async def _seed_session(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> str:
    sid = str(uuid4())
    async with factory() as s:
        await auth_repo.create_session(s, session_id=sid, user_id=user_id, expires_at=_future())
        await s.commit()
    return sid


async def _seed_engagement(
    factory: async_sessionmaker[AsyncSession], owner_id: UUID, *, archived: bool = False
) -> UUID:
    async with factory() as s:
        engagement = await eng_repo.create_engagement(
            s, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
        )
        if archived:
            engagement.status = "archived"
        await s.commit()
        await s.refresh(engagement)
        return cast(UUID, engagement.id)


async def _add_member(
    factory: async_sessionmaker[AsyncSession], engagement_id: UUID, user_id: UUID
) -> None:
    async with factory() as s:
        await eng_repo.add_member(s, engagement_id, user_id)
        await s.commit()


async def _seed_pending(
    factory: async_sessionmaker[AsyncSession], engagement_id: UUID, initiator_id: UUID
) -> UUID:
    async with factory() as s:
        row = await repo.create_request(
            s,
            engagement_id=engagement_id,
            chat_message_id=uuid4(),
            initiator_user_id=initiator_id,
            server_name="shell-exec",
            tool_name="run",
            args={"cmd": "hydra -P rockyou.txt ssh://10.0.0.5"},
            reasons=["credential_attack"],
        )
        await s.commit()
        return cast(UUID, row.id)


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --- GET list -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_200_for_member(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    await _seed_pending(factory, eng_id, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/approvals?status=pending",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["reasons"] == ["credential_attack"]


@pytest.mark.asyncio
async def test_list_404_for_non_member(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    owner = await _seed_user(factory, "owner")
    outsider = await _seed_user(factory, "outsider")
    sid = await _seed_session(factory, cast(UUID, outsider.id))
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/approvals", cookies={_SESSION_COOKIE: sid}
        )
    assert resp.status_code == 404


# --- POST approve / reject ------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_200_for_member(app_and_factory: AppFactory) -> None:
    app, factory, exec_run = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    req_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["tool_run_id"] is not None
    exec_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_by_initiator_self_approved(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    req_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    body = resp.json()
    assert body["self_approved"] is True
    assert body["acted_by_username"] == "owner"


@pytest.mark.asyncio
async def test_approve_by_other_member_cross_approval(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    owner = await _seed_user(factory, "owner")
    member = await _seed_user(factory, "second")
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    await _add_member(factory, eng_id, cast(UUID, member.id))
    sid = await _seed_session(factory, cast(UUID, member.id))
    # The owner is the initiator; a different member approves.
    req_id = await _seed_pending(factory, eng_id, cast(UUID, owner.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["self_approved"] is False
    assert body["acted_by_username"] == "second"


@pytest.mark.asyncio
async def test_reject_200(app_and_factory: AppFactory) -> None:
    app, factory, exec_run = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    req_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/reject",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    exec_run.assert_not_awaited()  # reject never executes the command


@pytest.mark.asyncio
async def test_decide_409_already_decided(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    req_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))
    # Decide it out-of-band so the request is already terminal.
    async with factory() as s:
        await repo.decide_request(
            s, request_id=req_id, status="rejected", acted_by_user_id=uuid4(), self_approved=False
        )
        await s.commit()

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["reason"] == "already_decided"
    assert body["status"] == "rejected"


@pytest.mark.asyncio
async def test_approve_409_archived(app_and_factory: AppFactory) -> None:
    app, factory, exec_run = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id), archived=True)
    req_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 409
    assert resp.json()["reason"] == "engagement_archived"
    exec_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_404_for_non_member(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    owner = await _seed_user(factory, "owner")
    outsider = await _seed_user(factory, "outsider")
    sid = await _seed_session(factory, cast(UUID, outsider.id))
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    req_id = await _seed_pending(factory, eng_id, cast(UUID, owner.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/approvals/{req_id}/approve",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_401(app_and_factory: AppFactory) -> None:
    app, factory, _ = app_and_factory
    user = await _seed_user(factory, "owner")
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.get(f"/api/v1/engagements/{eng_id}/approvals")
    assert resp.status_code == 401
