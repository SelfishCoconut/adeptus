"""Verifier tests (Slice 10 task 8) — the §14 tamper-detection guarantee.

Tampering is performed with raw Core UPDATE/DELETE to simulate an attacker with DB
access bypassing the append-only repository; the verifier reads authoritative DB
state (iter_chain_ordered uses populate_existing) and must catch every case.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.audit import repository, verify
from app.features.audit.hashing import compute_entry_hash
from app.features.audit.models import AuditEntry


async def _append_n(db: AsyncSession, n: int) -> list[AuditEntry]:
    entries = []
    for _ in range(n):
        e = await repository.append_entry(db, action="login", actor_user_id=uuid4())
        entries.append(e)
    await db.commit()
    return entries


async def test_verify_empty_chain_ok(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await verify.run(db_session)
    assert code == 0
    assert "0 entries verified" in capsys.readouterr().out


async def test_verify_clean_chain_exit_zero(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _append_n(db_session, 5)
    code = await verify.run(db_session)
    assert code == 0
    assert "5 entries verified" in capsys.readouterr().out


async def test_verify_detects_field_tamper(db_session: AsyncSession) -> None:
    await _append_n(db_session, 3)
    # Alter a content field (payload) of the middle row without touching its hash.
    await db_session.execute(
        update(AuditEntry).where(AuditEntry.seq == 2).values(payload={"tampered": True})
    )
    await db_session.commit()

    ok, _, broke = await verify.verify(db_session)
    assert not ok
    assert broke is not None
    assert broke.kind == "content-tamper"
    assert broke.seq == 2


async def test_verify_detects_deleted_middle_row(db_session: AsyncSession) -> None:
    await _append_n(db_session, 5)
    await db_session.execute(delete(AuditEntry).where(AuditEntry.seq == 3))
    await db_session.commit()

    ok, verified, broke = await verify.verify(db_session)
    assert not ok
    assert broke is not None
    assert broke.kind == "seq-gap"
    # Break surfaces at the row that should have been seq 3 but is seq 4.
    assert broke.seq == 4
    assert verified == 2


async def test_verify_detects_reordered_rows(db_session: AsyncSession) -> None:
    entries = await _append_n(db_session, 3)
    # Relink row 3 so it (internally-consistently) points at row 1 instead of row 2 —
    # a reorder/splice: its own hash is valid, but the prev_hash no longer chains.
    row3 = entries[2]
    wrong_prev = entries[0].entry_hash
    new_hash = compute_entry_hash(wrong_prev, verify._content_of(row3))
    await db_session.execute(
        update(AuditEntry)
        .where(AuditEntry.seq == 3)
        .values(prev_hash=wrong_prev, entry_hash=new_hash)
    )
    await db_session.commit()

    ok, _, broke = await verify.verify(db_session)
    assert not ok
    assert broke is not None
    assert broke.kind == "broken-link"
    assert broke.seq == 3


async def test_verify_detects_tail_truncation(db_session: AsyncSession) -> None:
    # Delete the LAST row: the chain scan stays internally consistent, but the
    # authoritative head pointer still references the truncated tail.
    await _append_n(db_session, 3)
    await db_session.execute(delete(AuditEntry).where(AuditEntry.seq == 3))
    await db_session.commit()

    ok, verified, broke = await verify.verify(db_session)
    assert not ok
    assert broke is not None
    assert broke.kind == "head-mismatch"
    assert verified == 2


async def test_verify_detects_missing_head(db_session: AsyncSession) -> None:
    # The authoritative head row vanishing (e.g. a truncation that dropped it) is a
    # hard failure, not a skipped check.
    from app.features.audit.models import AuditChainHead

    await _append_n(db_session, 2)
    await db_session.execute(delete(AuditChainHead))
    await db_session.commit()

    ok, _, broke = await verify.verify(db_session)
    assert not ok
    assert broke is not None
    assert broke.kind == "head-missing"


async def test_run_nonzero_exit_and_stderr_on_break(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _append_n(db_session, 2)
    await db_session.execute(
        update(AuditEntry).where(AuditEntry.seq == 1).values(actor_user_id=uuid4())
    )
    await db_session.commit()

    code = await verify.run(db_session)
    assert code == 1
    err = capsys.readouterr().err
    assert "AUDIT CHAIN BROKEN" in err
    assert "seq=1" in err
