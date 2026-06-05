"""Router tests — httpx.AsyncClient against the auth + personas app, real session-cookie auth.

The auth login emits hash-chained audit entries against SQLite (which ignores FOR UPDATE).
"""

from __future__ import annotations

import datetime
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.personas import service

_hasher = PasswordHasher()
_SESSION_COOKIE = "session_id"

pytestmark = pytest.mark.asyncio


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


async def _seed_builtins(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await service.bootstrap_system_personas(s)
        await s.commit()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_persona(
    client: httpx.AsyncClient, cookie: str, name: str, system_prompt: str = "p"
) -> httpx.Response:
    return await client.post(
        "/api/v1/personas",
        json={"name": name, "system_prompt": system_prompt},
        cookies={_SESSION_COOKIE: cookie},
    )


async def test_list_personas_200_builtins_and_own(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    await _seed_builtins(factory)
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        await _create_persona(client, cookie, "Mine")
        resp = await client.get("/api/v1/personas", cookies={_SESSION_COOKIE: cookie})

    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["items"]]
    assert {"General", "Recon", "Web Exploit", "Report Writer"}.issubset(set(names))
    assert "Mine" in names


async def test_create_persona_201(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await _create_persona(client, cookie, "Cloud", "cloud prompt")

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Cloud"
    assert body["is_builtin"] is False
    assert body["slug"] is None
    assert "user_id" not in body  # ownership never exposed


async def test_create_duplicate_name_409(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        await _create_persona(client, cookie, "Dup")
        resp = await _create_persona(client, cookie, "Dup")

    assert resp.status_code == 409


async def test_patch_own_persona_200(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        created = (await _create_persona(client, cookie, "A", "a")).json()
        resp = await client.patch(
            f"/api/v1/personas/{created['id']}",
            json={"system_prompt": "a2"},
            cookies={_SESSION_COOKIE: cookie},
        )

    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "a2"
    assert resp.json()["name"] == "A"


async def test_patch_builtin_404(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    await _seed_builtins(factory)
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        listed = (await client.get("/api/v1/personas", cookies={_SESSION_COOKIE: cookie})).json()
        builtin_id = next(p["id"] for p in listed["items"] if p["is_builtin"])
        resp = await client.patch(
            f"/api/v1/personas/{builtin_id}",
            json={"name": "Hacked"},
            cookies={_SESSION_COOKIE: cookie},
        )

    assert resp.status_code == 404


async def test_patch_other_user_404(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    alice = await _seed_user(factory, "alice")
    bob = await _seed_user(factory, "bob")
    alice_cookie = await _seed_session(factory, cast(UUID, alice.id))
    bob_cookie = await _seed_session(factory, cast(UUID, bob.id))

    async with _client(app) as client:
        bobs = (await _create_persona(client, bob_cookie, "Bobs")).json()
        resp = await client.patch(
            f"/api/v1/personas/{bobs['id']}",
            json={"name": "Stolen"},
            cookies={_SESSION_COOKIE: alice_cookie},
        )

    assert resp.status_code == 404


async def test_delete_own_persona_204(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        created = (await _create_persona(client, cookie, "A")).json()
        resp = await client.delete(
            f"/api/v1/personas/{created['id']}", cookies={_SESSION_COOKIE: cookie}
        )
        assert resp.status_code == 204
        # Gone now.
        listed = (await client.get("/api/v1/personas", cookies={_SESSION_COOKIE: cookie})).json()
    assert created["id"] not in [p["id"] for p in listed["items"]]


async def test_delete_builtin_404(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    await _seed_builtins(factory)
    user = await _seed_user(factory, "alice")
    cookie = await _seed_session(factory, cast(UUID, user.id))

    async with _client(app) as client:
        listed = (await client.get("/api/v1/personas", cookies={_SESSION_COOKIE: cookie})).json()
        builtin_id = next(p["id"] for p in listed["items"] if p["is_builtin"])
        resp = await client.delete(
            f"/api/v1/personas/{builtin_id}", cookies={_SESSION_COOKIE: cookie}
        )

    assert resp.status_code == 404


async def test_personas_unauthenticated_401(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _factory = app_and_factory
    async with _client(app) as client:
        resp = await client.get("/api/v1/personas")
    assert resp.status_code == 401
