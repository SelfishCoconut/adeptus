"""Ollama client tests — the HTTP transport is mocked (httpx.MockTransport).

No real Ollama is ever contacted (CLAUDE.md): a MockTransport handler returns canned
NDJSON, a non-2xx status, or raises a connection error, and we assert the client's
behaviour (token yields, usage capture, LlmUnreachableError, model/URL passthrough).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from app.core.config import get_settings
from app.features.chat import ollama_client
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage, stream_chat
from app.features.chat.schemas import OllamaChatMessage


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the required settings env so get_settings() can instantiate."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ndjson(*frames: dict[str, object]) -> bytes:
    return ("\n".join(json.dumps(f) for f in frames) + "\n").encode()


def _frame(content: str, *, done: bool = False, **extra: object) -> dict[str, object]:
    return {"message": {"role": "assistant", "content": content}, "done": done, **extra}


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    """Patch ollama_client._build_async_client to use a MockTransport. Returns a list
    that captures each outgoing request for assertions."""
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://ollama:11434", transport=httpx.MockTransport(_wrapped)
        )

    monkeypatch.setattr(ollama_client, "_build_async_client", _factory)
    return captured


async def _collect(messages: list[OllamaChatMessage], **kw: object) -> list[str]:
    return [tok async for tok in stream_chat(messages=messages, **kw)]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_yields_tokens_from_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _ndjson(_frame("Hel"), _frame("lo "), _frame("world"), _frame("", done=True))
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    tokens = await _collect([OllamaChatMessage(role="user", content="hi")])

    assert tokens == ["Hel", "lo ", "world"]
    assert "".join(tokens) == "Hello world"


@pytest.mark.asyncio
async def test_populates_usage_from_done_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _ndjson(
        _frame("hi"),
        _frame("", done=True, prompt_eval_count=26, eval_count=12),
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    usage = OllamaUsage()
    tokens = await _collect([OllamaChatMessage(role="user", content="hi")], usage=usage)

    assert tokens == ["hi"]
    assert usage.prompt_tokens == 26
    assert usage.completion_tokens == 12


@pytest.mark.asyncio
async def test_skips_empty_and_malformed_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b'{"message":{"content":"a"},"done":false}\nnot-json\n\n'
    raw += b'{"message":{"content":""},"done":false}\n'
    raw += b'{"message":{"content":"b"},"done":true}\n'
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=raw))

    tokens = await _collect([OllamaChatMessage(role="user", content="hi")])

    assert tokens == ["a", "b"]


@pytest.mark.asyncio
async def test_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    _patch_transport(monkeypatch, _boom)

    with pytest.raises(LlmUnreachableError):
        await _collect([OllamaChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, content=b"boom"))

    with pytest.raises(LlmUnreachableError):
        await _collect([OllamaChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_uses_configured_model_and_chat_path(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _ndjson(_frame("ok", done=True))
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    await _collect([OllamaChatMessage(role="user", content="hi")])

    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/api/chat"
    sent = json.loads(req.content)
    assert sent["model"] == "qwen3.5:9b"
    assert sent["stream"] is True
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_explicit_model_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _ndjson(_frame("ok", done=True))
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    await _collect([OllamaChatMessage(role="user", content="hi")], model="llama3:8b")

    sent = json.loads(captured[0].content)
    assert sent["model"] == "llama3:8b"
