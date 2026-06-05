#!/usr/bin/env python3
"""Deterministic Ollama stub for the approvals E2E (Slice 17).

CLAUDE.md forbids a real model in CI/tests and the chat E2E convention is to point
``ADEPTUS_OLLAMA_URL`` at a stub that returns a fixed reply — a live model cannot be
relied on to emit a *specific* ``propose_command`` tool-call for a *specific* target, so
an assertion-bearing approval E2E would otherwise be flaky. This stub mimics the
Ollama ``/api/chat`` streaming contract (see ``backend/app/features/chat/ollama_client.py``)
and always answers with one ``propose_command`` tool-call: a light, otherwise-autonomous
``httpx/run_httpx`` against ``OLLAMA_STUB_TARGET`` (default ``http://juice-shop:3000``).

The approvals spec declares an engagement scope that EXCLUDES that target's host, so the
proposal classifies ``out_of_scope`` (the only reason — httpx/run_httpx is light+network),
gates an approval card, and — because the target host is juice-shop — the approved run is
also sandbox-legal (the sandbox guard only permits juice-shop). Both conditions are met by
one fixed reply.

Usage (opt-in, local only — CI does not bring up the stack):

    python frontend/playwright/support/ollama-stub.py            # listens on :11434
    # then point the backend at it and run the stack + the guarded spec:
    ADEPTUS_OLLAMA_URL=http://host.docker.internal:11434 make dev
    E2E_STACK=1 ADEPTUS_ADMIN_PASSWORD=... pnpm playwright test approvals.spec.ts

Env:
    OLLAMA_STUB_PORT    listen port (default 11434)
    OLLAMA_STUB_TARGET  proposed httpx target (default http://juice-shop:3000)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TARGET = os.environ.get("OLLAMA_STUB_TARGET", "http://juice-shop:3000")
_PORT = int(os.environ.get("OLLAMA_STUB_PORT", "11434"))

# The fixed NDJSON reply, one JSON object per line, mirroring Ollama's /api/chat stream:
# a prose token, then a propose_command tool-call frame, then the terminal done frame.
_FRAMES: list[dict[str, object]] = [
    {"message": {"role": "assistant", "content": "Proposing a recon command."}, "done": False},
    {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "propose_command",
                        "arguments": {
                            "server": "httpx",
                            "tool": "run_httpx",
                            "args": {"target": _TARGET},
                            "rationale": "Baseline recon against the engagement target.",
                        },
                    }
                }
            ],
        },
        "done": False,
    },
    {
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "prompt_eval_count": 42,
        "eval_count": 12,
    },
]


def _ndjson_body() -> bytes:
    return ("".join(json.dumps(frame) + "\n" for frame in _FRAMES)).encode()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)  # drain the request body (model/messages/tools)
        if self.path.rstrip("/") != "/api/chat":
            self.send_error(404, "only /api/chat is stubbed")
            return
        body = _ndjson_body()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:  # silence per-request logging
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    print(f"ollama-stub listening on :{_PORT} → propose_command run_httpx {_TARGET}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
