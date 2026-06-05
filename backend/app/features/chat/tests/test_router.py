"""Router tests — HTTP (httpx.AsyncClient) + WebSocket (Starlette TestClient).

Ollama is mocked; the real audit ``record`` runs against SQLite (which ignores FOR
UPDATE). Auth uses a real session cookie seeded into the test DB.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator, Callable, Sequence
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.chat import repository as chat_repo
from app.features.chat import service
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo

_hasher = PasswordHasher()
_SESSION_COOKIE = "session_id"


def _future() -> datetime.datetime:
    return datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


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


async def _seed_pending(
    factory: async_sessionmaker[AsyncSession], engagement_id: UUID, user_id: UUID
) -> UUID:
    async with factory() as s:
        _, assistant = await chat_repo.insert_user_and_pending_assistant(
            s, engagement_id=engagement_id, user_id=user_id, content="hi"
        )
        await s.commit()
        return cast(UUID, assistant.id)


def _fake_stream(tokens: list[str]) -> Callable[..., AsyncIterator[str]]:
    async def _gen(
        *,
        messages: Sequence[OllamaChatMessage],
        model: str | None = None,
        usage: OllamaUsage | None = None,
    ) -> AsyncIterator[str]:
        for tok in tokens:
            yield tok
        if usage is not None:
            usage.prompt_tokens = 3
            usage.completion_tokens = 2

    return _gen


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _ws_client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# HTTP: POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_201_for_member(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "what is sqli?"},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_message"]["content"] == "what is sqli?"
    assert body["user_message"]["status"] == "complete"
    assert body["assistant_message"]["status"] == "pending"
    assert body["assistant_message"]["content"] == ""


@pytest.mark.asyncio
async def test_post_message_404_for_non_member(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    outsider = await _seed_user(factory, "outsider")
    sid = await _seed_session(factory, cast(UUID, outsider.id))
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi"},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_message_409_when_archived(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id), archived=True)

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi"},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_messages_unauthenticated_401(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    eng_id = uuid4()
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/engagements/{eng_id}/chat/messages")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# HTTP: GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_200_only_own(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    other = await _seed_user(factory, "other")
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    async with factory() as s:
        await eng_repo.add_member(s, engagement_id=eng_id, user_id=cast(UUID, other.id))
        await s.commit()
    owner_sid = await _seed_session(factory, cast(UUID, owner.id))

    async with _client(app) as client:
        # owner sends one, other sends one
        await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "owner-msg"},
            cookies={_SESSION_COOKIE: owner_sid},
        )
        other_sid = await _seed_session(factory, cast(UUID, other.id))
        await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "other-msg"},
            cookies={_SESSION_COOKIE: other_sid},
        )

        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            cookies={_SESSION_COOKIE: owner_sid},
        )
    assert resp.status_code == 200
    contents = [m["content"] for m in resp.json()["items"]]
    assert "owner-msg" in contents
    assert "other-msg" not in contents  # §5.4 per-user isolation


@pytest.mark.asyncio
async def test_list_messages_404_for_non_member(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    outsider = await _seed_user(factory, "outsider")
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    sid = await _seed_session(factory, cast(UUID, outsider.id))

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_streams_tokens_and_done(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory = app_and_factory
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["Hel", "lo"]))

    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    msg_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))

    client = _ws_client(app)
    frames: list[dict] = []
    with client.websocket_connect(f"/ws/chat/{msg_id}", cookies={_SESSION_COOKIE: sid}) as ws:
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break

    assert [f["type"] for f in frames] == ["token", "token", "done"]
    assert "".join(f["data"] for f in frames if f["type"] == "token") == "Hello"

    # The assistant row finalized complete with the joined content.
    async with factory() as s:
        row = await chat_repo.get_message_for_owner(
            s, message_id=msg_id, user_id=cast(UUID, user.id)
        )
    assert row is not None
    assert row.status == "complete"
    assert row.content == "Hello"


@pytest.mark.asyncio
async def test_ws_closes_4003_unauthenticated(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _factory = app_and_factory
    client = _ws_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/chat/{uuid4()}"):
            pass  # pragma: no cover
    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_closes_4003_for_non_owner(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    other = await _seed_user(factory, "other")
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    async with factory() as s:
        await eng_repo.add_member(s, engagement_id=eng_id, user_id=cast(UUID, other.id))
        await s.commit()
    msg_id = await _seed_pending(factory, eng_id, cast(UUID, owner.id))
    # `other` is a member of the engagement but does NOT own owner's message.
    other_sid = await _seed_session(factory, cast(UUID, other.id))

    client = _ws_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/chat/{msg_id}", cookies={_SESSION_COOKIE: other_sid}):
            pass  # pragma: no cover
    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_replays_completed_message(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory = app_and_factory

    def _boom(**_kw: object) -> AsyncIterator[str]:
        raise AssertionError("Ollama must not be called on replay")

    monkeypatch.setattr(service.ollama_client, "stream_chat", _boom)

    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    msg_id = await _seed_pending(factory, eng_id, cast(UUID, user.id))
    async with factory() as s:
        await chat_repo.finalize_assistant(
            s,
            message_id=msg_id,
            content="stored answer",
            status="complete",
            model="qwen3.5:9b",
            prompt_tokens=1,
            completion_tokens=1,
        )
        await s.commit()

    client = _ws_client(app)
    frames: list[dict] = []
    with client.websocket_connect(f"/ws/chat/{msg_id}", cookies={_SESSION_COOKIE: sid}) as ws:
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break

    assert [f["type"] for f in frames] == ["token", "done"]
    assert frames[0]["data"] == "stored answer"
