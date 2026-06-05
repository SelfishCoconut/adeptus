"""Anthropic client tests — the HTTP transport is mocked (httpx.MockTransport).

No real Anthropic API is ever contacted (CLAUDE.md): a MockTransport handler returns canned
SSE, a non-2xx status, or raises a connection error, and we assert the client's behaviour
(token yields from text_delta frames, usage capture, CloudNotConfiguredError when the key is
unset, LlmUnreachableError on transport failure, system hoisting, model pass-through, and that
the API key is sent only in the auth header and never logged — §3 / Risk 5).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest

from app.core.config import get_settings
from app.features.chat import anthropic_client
from app.features.chat.anthropic_client import CloudNotConfiguredError, stream_chat
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage
from app.features.chat.schemas import OllamaChatMessage

_API_KEY = "sk-ant-test-SECRETVALUE"  # gitleaks:allow — synthetic test key, not real


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the required settings env incl. a configured cloud key + model."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_API_KEY", _API_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sse(*frames: dict[str, object]) -> bytes:
    """Render frames as an Anthropic-style SSE byte stream (event: / data: / blank line)."""
    chunks = [f"event: {f['type']}\ndata: {json.dumps(f)}\n\n" for f in frames]
    return "".join(chunks).encode()


def _text_delta(text: str) -> dict[str, object]:
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}


def _start(input_tokens: int) -> dict[str, object]:
    return {"type": "message_start", "message": {"usage": {"input_tokens": input_tokens}}}


def _message_delta(output_tokens: int) -> dict[str, object]:
    return {"type": "message_delta", "usage": {"output_tokens": output_tokens}}


_STOP: dict[str, object] = {"type": "message_stop"}


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    """Patch anthropic_client._build_async_client to use a MockTransport; capture requests."""
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.anthropic.com", transport=httpx.MockTransport(_wrapped)
        )

    monkeypatch.setattr(anthropic_client, "_build_async_client", _factory)
    return captured


async def _collect(messages: list[OllamaChatMessage], **kw: object) -> list[str]:
    return [tok async for tok in stream_chat(messages=messages, **kw)]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_yields_tokens_from_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(_start(5), _text_delta("Hel"), _text_delta("lo "), _text_delta("world"), _STOP)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    tokens = await _collect([OllamaChatMessage(role="user", content="hi")])

    assert tokens == ["Hel", "lo ", "world"]
    assert "".join(tokens) == "Hello world"


@pytest.mark.asyncio
async def test_populates_usage_from_start_and_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(_start(26), _text_delta("hi"), _message_delta(12), _STOP)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    usage = OllamaUsage()
    tokens = await _collect([OllamaChatMessage(role="user", content="hi")], usage=usage)

    assert tokens == ["hi"]
    assert usage.prompt_tokens == 26
    assert usage.completion_tokens == 12


@pytest.mark.asyncio
async def test_skips_malformed_and_non_data_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"event: message_start\ndata: not-json\n\n"
    raw += _sse(_text_delta("a"), _text_delta(""), _text_delta("b"), _STOP)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=raw))

    tokens = await _collect([OllamaChatMessage(role="user", content="hi")])

    assert tokens == ["a", "b"]


@pytest.mark.asyncio
async def test_raises_cloud_not_configured_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADEPTUS_ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    # The transport must never be reached when the key is missing (no egress, §5.1).
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b""))

    with pytest.raises(CloudNotConfiguredError):
        await _collect([OllamaChatMessage(role="user", content="hi")])
    assert captured == []


@pytest.mark.asyncio
async def test_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    _patch_transport(monkeypatch, _boom)

    with pytest.raises(LlmUnreachableError):
        await _collect([OllamaChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, content=b"overloaded"))

    with pytest.raises(LlmUnreachableError):
        await _collect([OllamaChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_sends_key_in_header_and_hoists_system(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(_start(1), _text_delta("ok"), _STOP)
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    await _collect(
        [
            OllamaChatMessage(role="system", content="You are an assistant."),
            OllamaChatMessage(role="user", content="hi"),
        ]
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/v1/messages"
    assert req.headers["x-api-key"] == _API_KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    sent = json.loads(req.content)
    assert sent["model"] == "claude-sonnet-4-6"  # default (Resolved decision 1)
    assert sent["stream"] is True
    # The system entry is hoisted out of messages into the top-level param (no system role).
    assert sent["system"] == "You are an assistant."
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_explicit_model_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(_start(1), _text_delta("ok"), _STOP)
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    await _collect([OllamaChatMessage(role="user", content="hi")], model="claude-opus-4-8")

    sent = json.loads(captured[0].content)
    assert sent["model"] == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The instance key is sent only in the auth header — never logged (§3 / Risk 5)."""
    # A malformed SSE line exercises the warning path; a 500 on a second call exercises the
    # error path. Neither must ever emit the key.
    body = b"event: x\ndata: {bad-json\n\n" + _sse(_text_delta("hi"), _STOP)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    with caplog.at_level(logging.DEBUG):
        await _collect([OllamaChatMessage(role="user", content="hi")])
        _patch_transport(monkeypatch, lambda req: httpx.Response(500, content=b"boom"))
        with pytest.raises(LlmUnreachableError):
            await _collect([OllamaChatMessage(role="user", content="hi")])

    assert _API_KEY not in caplog.text


# ---------------------------------------------------------------------------
# Slice 16: native tool-calling surfacing (tool_use content blocks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_use_block_populates_proposed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.chat.tool_calling import ProposedCalls

    body = _sse(
        _start(5),
        _text_delta("Running "),
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "tu_1",
                "name": "propose_command",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"server": "shell-exec", '},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '"tool": "run", "args": {"cmd": "id"}}',
            },
        },
        {"type": "content_block_stop", "index": 1},
        _STOP,
    )
    captured = _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    proposed = ProposedCalls()
    tokens = [
        tok
        async for tok in stream_chat(
            messages=[OllamaChatMessage(role="user", content="hi")], proposed=proposed
        )
    ]
    assert tokens == ["Running "]
    assert len(proposed.calls) == 1
    assert proposed.calls[0].name == "propose_command"
    assert proposed.calls[0].arguments == {
        "server": "shell-exec",
        "tool": "run",
        "args": {"cmd": "id"},
    }
    sent = json.loads(captured[0].content)
    assert sent["tools"][0]["name"] == "propose_command"


@pytest.mark.asyncio
async def test_text_only_stream_leaves_proposed_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.chat.tool_calling import ProposedCalls

    body = _sse(_start(3), _text_delta("just prose"), _STOP)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    proposed = ProposedCalls()
    tokens = [
        tok
        async for tok in stream_chat(
            messages=[OllamaChatMessage(role="user", content="hi")], proposed=proposed
        )
    ]
    assert tokens == ["just prose"]
    assert proposed.calls == []
