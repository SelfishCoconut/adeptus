"""Pure canonical-serialization + hash helper for the audit chain.

This module is the **single source of truth** for how an audit entry is hashed.
Both the writer (``repository.append_entry``) and the verifier (``verify``) call
``compute_entry_hash`` — they MUST produce byte-identical canonical forms or every
entry would look tampered (Slice 10 Risk 2). It is deliberately pure: no DB, no I/O,
no clock — fully unit-testable.

Canonicalization strategy
-------------------------
The content fields are assembled into one dict and serialized with
``json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)``:

* **Field order** is fixed by ``sort_keys`` (alphabetical, recursively) — including
  the nested ``payload``, so the verifier reproduces the same bytes regardless of how
  Postgres JSONB reordered the payload keys on the way back out.
* **Timestamps** are formatted to a fixed microsecond precision in UTC, so the value
  hashed by the writer round-trips through ``timestamptz`` to the identical string.
* **NULLs** map to JSON ``null`` (a real sentinel that cannot collide with any string
  value), and JSON escaping removes any delimiter-injection ambiguity.
* **UUIDs** are stringified; ``self_approved`` stays a JSON bool; ``seq`` a JSON int.

``entry_hash = SHA-256( prev_hash_bytes || canonical_bytes )`` as lowercase hex.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

# 64 hex zeros — genesis prev_hash. Kept in sync with models.GENESIS_HASH via test.
GENESIS_HASH: str = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditContent:
    """The immutable set of fields that are hashed into an entry's ``entry_hash``.

    Defined here (next to the hasher that consumes it) rather than in schemas.py so
    the canonicalization contract lives in one pure module; schemas.py re-exports it.
    """

    seq: int
    created_at: datetime
    action: str
    actor_user_id: UUID | None
    engagement_id: UUID | None
    target_type: str | None
    target_id: str | None
    self_approved: bool | None
    payload: dict[str, Any]


def _format_timestamp(dt: datetime) -> str:
    """UTC ISO-8601 with fixed 6-digit microsecond precision and a ``Z`` suffix.

    Fixed precision is essential: a naive ``isoformat()`` drops the fractional part
    when microseconds are zero, which would make two logically-distinct serializations
    of the *same* instant differ. ``timestamptz`` stores microseconds, so the writer's
    value round-trips to the byte-identical string at verify time.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def canonical_bytes(content: AuditContent) -> bytes:
    """Deterministic byte serialization of the hashed content fields.

    Exposed (not just used internally) so tests can assert canonicalization directly.
    """
    obj: dict[str, Any] = {
        "seq": content.seq,
        "created_at": _format_timestamp(content.created_at),
        "action": content.action,
        "actor_user_id": str(content.actor_user_id) if content.actor_user_id else None,
        "engagement_id": str(content.engagement_id) if content.engagement_id else None,
        "target_type": content.target_type,
        "target_id": content.target_id,
        "self_approved": content.self_approved,
        "payload": content.payload,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_entry_hash(prev_hash: str, content: AuditContent) -> str:
    """Return the lowercase hex SHA-256 over ``prev_hash || canonical(content)``.

    ``prev_hash`` is the previous row's ``entry_hash`` (or ``GENESIS_HASH`` for the
    first entry). It is decoded from hex to raw bytes before hashing so the genesis
    all-zero hash contributes 32 zero bytes, not the ASCII string ``"00...0"``.

    Raises ``ValueError`` if ``prev_hash`` is not 64 hex characters — a corrupt link
    should fail loudly, not silently hash a malformed value.
    """
    prev = prev_hash.strip()
    if len(prev) != 64:
        raise ValueError(f"prev_hash must be 64 hex chars, got {len(prev)}")
    prev_bytes = bytes.fromhex(prev)  # raises ValueError on non-hex
    return hashlib.sha256(prev_bytes + canonical_bytes(content)).hexdigest()
