"""Router tests — HTTP (httpx.AsyncClient) + WebSocket (Starlette TestClient).

Ollama is mocked; the real audit ``record`` runs against SQLite (which ignores FOR
UPDATE). Auth uses a real session cookie seeded into the test DB.
"""

from __future__ import annotations

import datetime
import json
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
from app.features.chat import plan_parser, service
from app.features.chat import repository as chat_repo
from app.features.chat.ollama_client import OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.engagements import repository as eng_repo
from app.features.graph import repository as graph_repo
from app.features.personas import service as personas_service

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
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    *,
    archived: bool = False,
    cloud: bool = False,
) -> UUID:
    async with factory() as s:
        engagement = await eng_repo.create_engagement(
            s, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
        )
        if archived:
            engagement.status = "archived"
        if cloud:
            engagement.privacy_mode = "cloud_enabled"
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


async def _seed_node(
    factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
    *,
    node_type: str = "host",
    label: str = "h",
) -> UUID:
    async with factory() as s:
        node = await graph_repo.insert_node(
            s, engagement_id=engagement_id, node_type=node_type, label=label, properties={}
        )
        await s.commit()
        return cast(UUID, node.id)


async def _drain_ws(app: FastAPI, message_id: UUID, sid: str) -> None:
    """Connect the streaming WS and read to a terminal frame so the turn finalizes."""
    client = _ws_client(app)
    with client.websocket_connect(f"/ws/chat/{message_id}", cookies={_SESSION_COOKIE: sid}) as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break


def _meta_block(plan: list[dict], claims: list[dict]) -> str:
    """A well-formed trailing <adeptus-meta> block for the fake stream (Slice 13)."""
    payload = {"plan": plan, "claims": claims}
    return f"{plan_parser.START_MARKER}\n{json.dumps(payload)}\n{plan_parser.END_MARKER}"


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


# Synthetic secret vector for the egress-friction POST tests; carries gitleaks:allow.
_SECRET_MESSAGE = "creds AKIAIOSFODNN7EXAMPLE password=hunter2"  # gitleaks:allow


@pytest.mark.asyncio
async def test_post_cloud_secret_unconfirmed_409_with_categories(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A cloud-enabled secret-bearing POST without confirmation → 409 with category names."""
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id), cloud=True)

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": _SECRET_MESSAGE},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["reason"] == "egress_secret_flagged"
    assert "aws_access_key" in body["matched_categories"]
    # The 409 body carries category NAMES only — never the matched secret value (§5.5).
    assert "AKIAIOSFODNN7EXAMPLE" not in resp.text  # gitleaks:allow


@pytest.mark.asyncio
async def test_post_cloud_secret_confirmed_201(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Re-POSTing with confirmed_egress=true clears the friction and persists the pair."""
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id), cloud=True)

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": _SECRET_MESSAGE, "confirmed_egress": True},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 201
    # The persisted user content is byte-for-byte the input — never redacted (§5.5).
    assert resp.json()["user_message"]["content"] == _SECRET_MESSAGE


@pytest.mark.asyncio
async def test_post_archived_409_reason_archived(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """The archived 409 shares the EgressConfirmationRequired body with a distinguishing reason."""
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
    body = resp.json()
    assert body["reason"] == "engagement_archived"
    assert body["matched_categories"] == []


@pytest.mark.asyncio
async def test_post_local_only_secret_201_no_friction(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A secret on a local_only engagement POSTs 201 with no friction (no egress to gate)."""
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))  # local_only (default)

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": _SECRET_MESSAGE},
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 201


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


# ---------------------------------------------------------------------------
# HTTP: POST with §5.3 node-id inputs + GET debug (Slice 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_with_node_ids_201(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    node_id = await _seed_node(factory, eng_id, label="pinned-box")

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={
                "content": "hi",
                "pinned_node_ids": [str(node_id)],
                "recent_node_ids": [str(node_id)],
                "mentioned_node_ids": [],
            },
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 201
    assert resp.json()["assistant_message"]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_debug_200_for_owner(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory = app_and_factory
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["the ", "answer"]))
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    node_id = await _seed_node(factory, eng_id, label="pinned-box")

    async with _client(app) as client:
        post = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi", "pinned_node_ids": [str(node_id)]},
            cookies={_SESSION_COOKIE: sid},
        )
    assistant_id = post.json()["assistant_message"]["id"]
    # Stream to finalize so the canonical §14 debug record is persisted.
    await _drain_ws(app, UUID(assistant_id), sid)

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages/{assistant_id}/debug",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["message_id"] == assistant_id
    assert [n["label"] for n in body["nodes"]] == ["pinned-box"]
    assert "pinned" in body["nodes"][0]["reasons"]
    assert body["model_output"] == "the answer"


@pytest.mark.asyncio
async def test_get_debug_404_for_non_owner(
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
    other_sid = await _seed_session(factory, cast(UUID, other.id))

    async with _client(app) as client:
        post = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "private"},
            cookies={_SESSION_COOKIE: owner_sid},
        )
        assistant_id = post.json()["assistant_message"]["id"]
        # `other` is a member but does not own owner's turn → 404 (§5.4 / Risk 5).
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages/{assistant_id}/debug",
            cookies={_SESSION_COOKIE: other_sid},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_debug_404_for_non_member(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    outsider = await _seed_user(factory, "outsider")
    eng_id = await _seed_engagement(factory, cast(UUID, owner.id))
    owner_sid = await _seed_session(factory, cast(UUID, owner.id))
    outsider_sid = await _seed_session(factory, cast(UUID, outsider.id))

    async with _client(app) as client:
        post = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "private"},
            cookies={_SESSION_COOKIE: owner_sid},
        )
        assistant_id = post.json()["assistant_message"]["id"]
        # A non-member cannot even learn the engagement exists → 404 (§17.1).
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages/{assistant_id}/debug",
            cookies={_SESSION_COOKIE: outsider_sid},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_debug_401_unauthenticated(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _factory = app_and_factory
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{uuid4()}/chat/messages/{uuid4()}/debug",
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Slice 13 — changed response models carry plan/claims through the routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_response_has_plan_field(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory = app_and_factory
    block = _meta_block(
        [{"step": "do x", "status": "done"}], [{"text": "maybe apache", "certainty": 45}]
    )
    monkeypatch.setattr(
        service.ollama_client, "stream_chat", _fake_stream(["Visible answer.", "\n\n", block])
    )
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))

    async with _client(app) as client:
        post = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi"},
            cookies={_SESSION_COOKIE: sid},
        )
    assistant_id = post.json()["assistant_message"]["id"]
    await _drain_ws(app, UUID(assistant_id), sid)

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    assistant = next(m for m in resp.json()["items"] if m["role"] == "assistant")
    assert assistant["plan"] == [{"step": "do x", "status": "done"}]
    assert assistant["claims"][0]["certainty"] == 45
    assert assistant["claims"][0]["node_id"] is None
    assert "adeptus-meta" not in assistant["content"]  # block stripped from stored prose


@pytest.mark.asyncio
async def test_get_debug_response_has_plan_and_claims(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory = app_and_factory
    block = _meta_block(
        [{"step": "enumerate", "status": "in_progress"}], [{"text": "unsure", "certainty": 20}]
    )
    monkeypatch.setattr(service.ollama_client, "stream_chat", _fake_stream(["Prose.", "\n", block]))
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))

    async with _client(app) as client:
        post = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi"},
            cookies={_SESSION_COOKIE: sid},
        )
    assistant_id = post.json()["assistant_message"]["id"]
    await _drain_ws(app, UUID(assistant_id), sid)

    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/engagements/{eng_id}/chat/messages/{assistant_id}/debug",
            cookies={_SESSION_COOKIE: sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"][0]["step"] == "enumerate"
    assert body["claims"][0]["certainty"] == 20
    # §14: the debug view shows the UNSTRIPPED output (block included).
    assert "adeptus-meta" in body["model_output"]


# ---------------------------------------------------------------------------
# HTTP: POST/GET persona threading (Slice 15)
# ---------------------------------------------------------------------------


async def _seed_builtin_personas(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await personas_service.bootstrap_system_personas(s)
        await s.commit()


async def _seed_custom_persona(
    factory: async_sessionmaker[AsyncSession], owner: User, name: str, prompt: str
) -> UUID:
    async with factory() as s:
        created = await personas_service.create_persona(
            s, requester=owner, name=name, system_prompt=prompt
        )
        await s.commit()
        return created.id


@pytest.mark.asyncio
async def test_post_message_accepts_persona_id(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    await _seed_builtin_personas(factory)
    persona_id = await _seed_custom_persona(factory, user, "Recon X", "recon prompt")

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "where do I start?", "persona_id": str(persona_id)},
            cookies={_SESSION_COOKIE: sid},
        )
        assert resp.status_code == 201
        listed = (
            await client.get(
                f"/api/v1/engagements/{eng_id}/chat/messages", cookies={_SESSION_COOKIE: sid}
            )
        ).json()

    assistant = next(m for m in listed["items"] if m["role"] == "assistant")
    assert assistant["persona_id"] == str(persona_id)
    assert assistant["persona_name"] == "Recon X"


@pytest.mark.asyncio
async def test_post_foreign_persona_still_201_general_fallback(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """§17.1 — a foreign persona id never errors; the turn falls back to general."""
    app, factory = app_and_factory
    alice = await _seed_user(factory, "alice")
    bob = await _seed_user(factory, "bob")
    sid = await _seed_session(factory, cast(UUID, alice.id))
    eng_id = await _seed_engagement(factory, cast(UUID, alice.id))
    await _seed_builtin_personas(factory)
    bobs_persona = await _seed_custom_persona(factory, bob, "Bobs", "BOB-SECRET")

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi", "persona_id": str(bobs_persona)},
            cookies={_SESSION_COOKIE: sid},
        )
        assert resp.status_code == 201
        listed = (
            await client.get(
                f"/api/v1/engagements/{eng_id}/chat/messages", cookies={_SESSION_COOKIE: sid}
            )
        ).json()

    assistant = next(m for m in listed["items"] if m["role"] == "assistant")
    assert assistant["persona_name"] == "General"  # not Bob's persona


@pytest.mark.asyncio
async def test_list_messages_response_carries_persona_fields(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, factory = app_and_factory
    user = await _seed_user(factory, "owner")
    sid = await _seed_session(factory, cast(UUID, user.id))
    eng_id = await _seed_engagement(factory, cast(UUID, user.id))
    await _seed_builtin_personas(factory)
    persona_id = await _seed_custom_persona(factory, user, "Recon X", "recon prompt")

    async with _client(app) as client:
        await client.post(
            f"/api/v1/engagements/{eng_id}/chat/messages",
            json={"content": "hi", "persona_id": str(persona_id)},
            cookies={_SESSION_COOKIE: sid},
        )
        listed = (
            await client.get(
                f"/api/v1/engagements/{eng_id}/chat/messages", cookies={_SESSION_COOKIE: sid}
            )
        ).json()

    user_msg = next(m for m in listed["items"] if m["role"] == "user")
    assistant = next(m for m in listed["items"] if m["role"] == "assistant")
    # The user turn never carries a persona; the assistant turn does.
    assert user_msg["persona_id"] is None
    assert user_msg["persona_name"] is None
    assert assistant["persona_id"] == str(persona_id)
    assert assistant["persona_name"] == "Recon X"
