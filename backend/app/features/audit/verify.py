"""``verify-chain`` CLI — the §14 tamper-detection guarantee.

Run as ``python -m app.features.audit.verify`` (wrapped by ``make verify-audit``). It
streams the audit chain in ``seq`` order and, using the SAME pure ``compute_entry_hash``
the writer uses (Risk 2 — no drift), checks three invariants per entry plus a final
head cross-check:

  * **content-tamper** — the stored ``entry_hash`` no longer matches a recompute over the
    stored content (a field was altered).
  * **seq-gap** — ``seq`` is not the contiguous successor (a middle row was deleted, or a
    row was inserted/duplicated).
  * **broken-link** — ``prev_hash`` does not equal the previous entry's ``entry_hash`` (a
    row was reordered or relinked).
  * **head-mismatch** / **head-missing** — after the scan, ``audit_chain_head`` still
    points at a seq/hash that the table no longer ends with (the tail was truncated), or
    the authoritative head row is gone entirely. This is why the head pointer is
    authoritative, not derived from ``MAX(seq)``.

Exit 0 + ``audit chain OK — N entries verified`` on an intact chain; exit 1 and a
description of the FIRST break (its ``seq``/``id``/expected-vs-actual) otherwise.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.features.audit import repository
from app.features.audit.hashing import GENESIS_HASH, AuditContent, compute_entry_hash
from app.features.audit.models import AuditEntry


@dataclass(frozen=True)
class ChainBreak:
    """The first detected divergence from a valid chain."""

    seq: int
    entry_id: str
    kind: str
    expected: str
    actual: str

    def describe(self) -> str:
        return (
            f"AUDIT CHAIN BROKEN at seq={self.seq} id={self.entry_id} kind={self.kind}\n"
            f"  expected: {self.expected}\n"
            f"  actual:   {self.actual}"
        )


def _content_of(entry: AuditEntry) -> AuditContent:
    """Reconstruct the hashed content from a stored row (the verifier's view of truth)."""
    return AuditContent(
        seq=entry.seq,
        created_at=entry.created_at,
        action=entry.action,
        # Runtime values are python uuid.UUID; the Mapped[] type is the SQLAlchemy
        # column type per the repo's convention, so narrow here for the pure hasher.
        actor_user_id=cast("UUID | None", entry.actor_user_id),
        engagement_id=cast("UUID | None", entry.engagement_id),
        target_type=entry.target_type,
        target_id=entry.target_id,
        self_approved=entry.self_approved,
        payload=dict(entry.payload) if entry.payload else {},
    )


def check_entry(entry: AuditEntry, *, expected_seq: int, expected_prev: str) -> ChainBreak | None:
    """Validate one entry against its expected position + predecessor. None == intact.

    Order matters for which kind is reported when several fail at once: a deleted middle
    row breaks BOTH seq and linkage — we surface ``seq-gap`` (the more diagnostic cause).
    """
    entry_id = str(entry.id)
    stored_hash = entry.entry_hash.strip()

    if entry.seq != expected_seq:
        return ChainBreak(
            seq=entry.seq,
            entry_id=entry_id,
            kind="seq-gap",
            expected=f"seq {expected_seq}",
            actual=f"seq {entry.seq}",
        )

    recomputed = compute_entry_hash(entry.prev_hash, _content_of(entry))
    if recomputed != stored_hash:
        return ChainBreak(
            seq=entry.seq,
            entry_id=entry_id,
            kind="content-tamper",
            expected=recomputed,
            actual=stored_hash,
        )

    if entry.prev_hash.strip() != expected_prev:
        return ChainBreak(
            seq=entry.seq,
            entry_id=entry_id,
            kind="broken-link",
            expected=expected_prev,
            actual=entry.prev_hash.strip(),
        )

    return None


async def verify(db: AsyncSession) -> tuple[bool, int, ChainBreak | None]:
    """Stream + verify the whole chain. Returns (ok, entries_verified, first_break)."""
    expected_seq = 1
    expected_prev = GENESIS_HASH
    verified = 0
    last_seq = 0
    last_hash = GENESIS_HASH

    async for entry in repository.iter_chain_ordered(db):
        broke = check_entry(entry, expected_seq=expected_seq, expected_prev=expected_prev)
        if broke is not None:
            return False, verified, broke
        verified += 1
        last_seq = entry.seq
        last_hash = entry.entry_hash.strip()
        expected_seq = entry.seq + 1
        expected_prev = last_hash

    # Head cross-check: the authoritative head pointer must match the chain's actual tail.
    head = await repository.get_chain_head(db)
    if head is None:
        # The authoritative head is seeded by the migration and never legitimately
        # removed; its absence is itself tampering (e.g. a truncation that also dropped
        # the head row), so it is a hard failure rather than a skipped check.
        return (
            False,
            verified,
            ChainBreak(
                seq=last_seq,
                entry_id="<chain-head>",
                kind="head-missing",
                expected="a seeded audit_chain_head row",
                actual="no audit_chain_head row",
            ),
        )
    if head.last_seq != last_seq or head.head_hash.strip() != last_hash:
        return (
            False,
            verified,
            ChainBreak(
                seq=head.last_seq,
                entry_id="<chain-head>",
                kind="head-mismatch",
                expected=f"seq {head.last_seq} / {head.head_hash.strip()}",
                actual=f"seq {last_seq} / {last_hash}",
            ),
        )

    return True, verified, None


async def run(db: AsyncSession) -> int:
    """Verify and print a human-readable result. Returns the process exit code."""
    ok, verified, broke = await verify(db)
    if ok:
        print(f"audit chain OK — {verified} entries verified")
        return 0
    assert broke is not None
    print(broke.describe(), file=sys.stderr)
    print(f"verified {verified} entries before the break", file=sys.stderr)
    return 1


async def _amain() -> int:
    factory = get_sessionmaker()
    async with factory() as db:
        return await run(db)


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
