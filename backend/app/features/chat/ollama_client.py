"""Thin async client over the local Ollama streaming chat API (Slice 11).

This is the SINGLE egress point from the backend to the local model. Per CLAUDE.md
every unit/component test mocks it — no test ever reaches a real Ollama (the HTTP
transport is replaced with ``httpx.MockTransport`` in ``test_ollama_client``).

The model is reached over plain HTTP at ``ADEPTUS_OLLAMA_URL/api/chat`` with
``stream=true``; Ollama answers with NDJSON, one JSON object per line:

    {"message": {"role": "assistant", "content": "Hel"}, "done": false}
    {"message": {"role": "assistant", "content": "lo"},  "done": false}
    {"message": {"role": "assistant", "content": ""}, "done": true,
     "prompt_eval_count": 26, "eval_count": 12}

Each frame's ``message.content`` is an incremental token slice; the final frame carries
``done: true`` plus the raw token counts. A connection failure or a non-2xx status
raises ``LlmUnreachableError`` — the service persists the turn ``failed`` and surfaces a
stable, non-leaky reason to the WebSocket (§5.1: no automatic fallback, no redaction).

§5.1: a slow-but-progressing model is acceptable and must NOT trigger fallback, so the
HTTP read timeout is disabled (only the connect timeout is bounded, so a truly
unreachable Ollama fails fast). The wedged-socket safety valve (no-token-progress cap)
lives in the service layer, not here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.features.chat.schemas import OllamaChatMessage

logger = logging.getLogger(__name__)

# Bound the connect/write/pool phases so an unreachable Ollama fails fast, but leave the
# read phase unbounded so a slow-but-streaming model is never aborted (§5.1).
_CONNECT_TIMEOUT_SECONDS = 5.0


class LlmUnreachableError(Exception):
    """Raised when the local model cannot be reached or returns a non-2xx status.

    Caught in the chat service, which persists the assistant turn ``failed`` and emits
    a stable WS ``error`` frame. Deliberately NOT a core error type: it is never
    translated to an HTTP status (the POST endpoint does not call Ollama — only the WS
    streaming path does).
    """


@dataclass
class OllamaUsage:
    """Mutable holder for the raw token counts on the final Ollama frame.

    Passed into :func:`stream_chat`; populated when the ``done`` frame arrives so the
    caller can read it *after* the token iterator is exhausted (an async generator
    cannot ``return`` a value). Both fields stay ``None`` if Ollama omits the counts.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _build_async_client() -> httpx.AsyncClient:
    """Construct the httpx client for the configured Ollama URL.

    Seam for tests: ``test_ollama_client`` patches this to return a client backed by an
    ``httpx.MockTransport`` so no real Ollama is contacted.
    """
    settings = get_settings()
    timeout = httpx.Timeout(_CONNECT_TIMEOUT_SECONDS, read=None)
    return httpx.AsyncClient(base_url=settings.ADEPTUS_OLLAMA_URL, timeout=timeout)


async def stream_chat(
    *,
    messages: Sequence[OllamaChatMessage],
    model: str | None = None,
    usage: OllamaUsage | None = None,
) -> AsyncIterator[str]:
    """Stream an assistant reply from the local model token-by-token.

    Args:
        messages: The ``messages`` array (system + conversation window + new user turn),
            sent verbatim — no redaction (§5.5).
        model: Ollama model name; defaults to ``ADEPTUS_LLM_MODEL`` (ADR-0004).
        usage: Optional holder populated with the final token counts when the stream
            completes (read it after iterating).

    Yields:
        Incremental assistant text slices (may be empty strings, which are skipped).

    Raises:
        LlmUnreachableError: connection failure, timeout, or a non-2xx response.
    """
    settings = get_settings()
    model_name = model or settings.ADEPTUS_LLM_MODEL
    payload = {
        "model": model_name,
        "messages": [m.model_dump() for m in messages],
        "stream": True,
    }

    try:
        async with _build_async_client() as client:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    raise LlmUnreachableError(
                        f"Ollama returned HTTP {response.status_code} for model {model_name!r}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        # Tolerate a malformed line rather than aborting a live stream.
                        logger.warning("Skipping malformed Ollama NDJSON line: %r", line)
                        continue

                    token = frame.get("message", {}).get("content", "")
                    if token:
                        yield token

                    if frame.get("done"):
                        if usage is not None:
                            usage.prompt_tokens = frame.get("prompt_eval_count")
                            usage.completion_tokens = frame.get("eval_count")
                        return
    except httpx.HTTPError as exc:
        # ConnectError / ReadError / timeouts all subclass httpx.HTTPError. Collapse to
        # the domain error with a stable message (the raw exception may leak host:port).
        raise LlmUnreachableError("Local model is unreachable") from exc
