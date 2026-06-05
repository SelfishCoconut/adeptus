"""Thin async client over the Anthropic Claude Messages streaming API (Slice 14, §5.1).

This is the SINGLE cloud egress point from the backend. It is used ONLY on a
``cloud_enabled`` engagement; per CLAUDE.md every unit/component test mocks it (the HTTP
transport is replaced with ``httpx.MockTransport`` in ``test_anthropic_client``) — no test
ever reaches the real Anthropic API.

It mirrors ``ollama_client.stream_chat``'s signature EXACTLY (Decision 2) so the chat streamer
reuses all of its buffering / sentinel-stripping / finalize / audit machinery verbatim; the
only branch is *which* client is iterated. The shared ``OllamaChatMessage`` array is mapped to
the Messages API shape here: a leading ``system`` entry is hoisted into the top-level
``system`` param (Anthropic rejects a ``system`` role inside ``messages``); the rest become the
``messages`` array, sent verbatim — no redaction (§5.5).

Wire format (per the Anthropic Messages API): ``POST {base}/v1/messages`` with ``stream:true``,
authenticated via the ``x-api-key`` header + ``anthropic-version``. The response is an SSE
stream; assistant text arrives in ``content_block_delta`` / ``text_delta`` frames, the prompt
token count on ``message_start`` (``usage.input_tokens``) and the completion count on
``message_delta`` (``usage.output_tokens``). Adaptive thinking is deliberately NOT requested:
the cloud client must yield plain assistant text into the same machinery the local path feeds,
so the turn shape (clean prose + the Slice-13 ``<adeptus-meta>`` block) is identical.

The API key is sent ONLY in the auth header and is NEVER logged or echoed (§3 / Risk 5): a
non-2xx body is read and discarded, and the raised error messages are stable and key-free.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.config import get_settings
from app.features.chat import tool_calling
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage
from app.features.chat.schemas import OllamaChatMessage
from app.features.chat.tool_calling import ProposedCalls, ProposedToolCall

logger = logging.getLogger(__name__)

# Bound connect/write/pool so an unreachable API fails fast; leave the read phase unbounded so
# a slow-but-streaming response is never aborted (mirrors ollama_client / §5.1).
_CONNECT_TIMEOUT_SECONDS = 5.0

# Pinned Messages API version (Resolved decision 1 keeps model/base-url in settings; the wire
# version is a client-level constant).
_ANTHROPIC_VERSION = "2023-06-01"

# Output cap for one chat turn (assistant prose + the trailing <adeptus-meta> block). Generous
# enough to avoid mid-answer truncation, well under Sonnet's streaming ceiling. This is a
# request requirement of the Messages API, NOT a cost cap (§5.1 — no enforcement/hard caps).
_MAX_OUTPUT_TOKENS = 4096


class CloudNotConfiguredError(Exception):
    """Raised when a cloud turn is attempted but ``ADEPTUS_ANTHROPIC_API_KEY`` is unset.

    Distinct from ``LlmUnreachableError`` (a transport failure): a missing key is a
    configuration problem, not an outage. The chat streamer translates it to a failed turn
    with ``CLOUD_NOT_CONFIGURED_MESSAGE`` and does NOT fall back to local (§5.1)."""


def _build_async_client() -> httpx.AsyncClient:
    """Construct the httpx client for the configured Anthropic base URL.

    Seam for tests: ``test_anthropic_client`` patches this to return a client backed by an
    ``httpx.MockTransport`` so no real Anthropic API is contacted (CLAUDE.md)."""
    settings = get_settings()
    timeout = httpx.Timeout(_CONNECT_TIMEOUT_SECONDS, read=None)
    return httpx.AsyncClient(base_url=settings.ADEPTUS_ANTHROPIC_BASE_URL, timeout=timeout)


def _to_messages_payload(
    messages: Sequence[OllamaChatMessage],
) -> tuple[list[str], list[dict[str, str]]]:
    """Split the shared messages array into (system parts, conversation turns).

    Anthropic carries the system prompt in a top-level ``system`` param, not as a message
    role, so every ``system`` entry is hoisted out; the remaining user/assistant turns are
    forwarded verbatim (§5.5 — no redaction)."""
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        else:
            conversation.append({"role": message.role, "content": message.content})
    return system_parts, conversation


async def stream_chat(
    *,
    messages: Sequence[OllamaChatMessage],
    model: str | None = None,
    usage: OllamaUsage | None = None,
    proposed: ProposedCalls | None = None,
) -> AsyncIterator[str]:
    """Stream an assistant reply from Claude token-by-token (Messages API, SSE).

    Args:
        messages: The system + conversation window + new user turn, sent verbatim (§5.5).
            A leading ``system`` entry is hoisted into the top-level ``system`` param.
        model: Claude model name; defaults to ``ADEPTUS_ANTHROPIC_MODEL`` (claude-sonnet-4-6).
        usage: Optional holder populated with the final token counts (read after iterating).
        proposed: Optional ``ProposedCalls`` holder (Slice 16). When provided, the
            ``propose_command`` tool is sent and ``tool_use`` content blocks are accumulated
            into it out-of-band (text deltas still stream unchanged); read after iterating.

    Yields:
        Incremental assistant text slices (empty deltas are skipped).

    Raises:
        CloudNotConfiguredError: ``ADEPTUS_ANTHROPIC_API_KEY`` is unset (no auto-fallback, §5.1).
        LlmUnreachableError: connection failure, timeout, or a non-2xx response.
    """
    settings = get_settings()
    api_key = settings.ADEPTUS_ANTHROPIC_API_KEY
    if not api_key:
        raise CloudNotConfiguredError("Cloud LLM is not configured for this engagement")
    model_name = model or settings.ADEPTUS_ANTHROPIC_MODEL

    system_parts, conversation = _to_messages_payload(messages)
    payload: dict[str, Any] = {
        "model": model_name,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "stream": True,
        "messages": conversation,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if proposed is not None:
        payload["tools"] = tool_calling.anthropic_tools()

    # Per-index accumulators for streamed tool_use blocks (Slice 16): index → (name, json buf).
    tool_blocks: dict[int, dict[str, str]] = {}

    headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    try:
        async with _build_async_client() as client:
            async with client.stream(
                "POST", "/v1/messages", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    # Drain+discard the body so the raised message is stable and the request
                    # content / key can never leak into a log line (Risk 5/7).
                    await response.aread()
                    raise LlmUnreachableError(
                        f"Anthropic API returned HTTP {response.status_code} for model "
                        f"{model_name!r}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    try:
                        frame = json.loads(data)
                    except json.JSONDecodeError:
                        # Never log the data itself (it carries assistant content, §5.5).
                        logger.warning("Skipping malformed Anthropic SSE data line")
                        continue

                    frame_type = frame.get("type")
                    if frame_type == "content_block_start":
                        if proposed is not None:
                            _begin_tool_block(frame, tool_blocks)
                    elif frame_type == "content_block_delta":
                        delta = frame.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                        elif delta.get("type") == "input_json_delta" and proposed is not None:
                            _accumulate_tool_input(frame, delta, tool_blocks)
                    elif frame_type == "content_block_stop":
                        if proposed is not None:
                            _finish_tool_block(frame, tool_blocks, proposed)
                    elif frame_type == "message_start":
                        if usage is not None:
                            counts = frame.get("message", {}).get("usage", {})
                            usage.prompt_tokens = counts.get("input_tokens")
                    elif frame_type == "message_delta":
                        # output_tokens is cumulative; the last message_delta carries the total.
                        if usage is not None:
                            output_tokens = frame.get("usage", {}).get("output_tokens")
                            if output_tokens is not None:
                                usage.completion_tokens = output_tokens
                    elif frame_type == "message_stop":
                        return
    except httpx.HTTPError as exc:
        # Collapse every transport error to the domain error with a stable, key-free message.
        raise LlmUnreachableError("Cloud model is unreachable") from exc


def _begin_tool_block(frame: dict[str, Any], tool_blocks: dict[int, dict[str, str]]) -> None:
    """Start accumulating a ``tool_use`` content block (Slice 16)."""
    block = frame.get("content_block", {})
    if block.get("type") != "tool_use":
        return
    index = frame.get("index")
    name = block.get("name")
    if isinstance(index, int) and isinstance(name, str):
        tool_blocks[index] = {"name": name, "json": ""}


def _accumulate_tool_input(
    frame: dict[str, Any], delta: dict[str, Any], tool_blocks: dict[int, dict[str, str]]
) -> None:
    """Append an ``input_json_delta`` fragment to its block's JSON buffer (Slice 16)."""
    index = frame.get("index")
    if isinstance(index, int) and index in tool_blocks:
        partial = delta.get("partial_json", "")
        if isinstance(partial, str):
            tool_blocks[index]["json"] += partial


def _finish_tool_block(
    frame: dict[str, Any], tool_blocks: dict[int, dict[str, str]], proposed: ProposedCalls
) -> None:
    """Finalize a ``tool_use`` block: parse its accumulated JSON into a ProposedToolCall."""
    index = frame.get("index")
    if not isinstance(index, int):
        return
    block = tool_blocks.pop(index, None)
    if block is None:
        return
    raw = block["json"].strip()
    try:
        arguments = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    proposed.calls.append(ProposedToolCall(name=block["name"], arguments=arguments))
