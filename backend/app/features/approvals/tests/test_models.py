"""Unit tests for the ApprovalRequest ORM model (Slice 16 task 1)."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.approvals.models import APPROVAL_STATUSES, ApprovalRequest


def _new_request(**overrides: object) -> ApprovalRequest:
    fields: dict[str, object] = {
        "engagement_id": uuid4(),
        "chat_message_id": uuid4(),
        "initiator_user_id": uuid4(),
        "server_name": "shell-exec",
        "tool_name": "run",
        "args": {"cmd": "hydra -l admin -P rockyou.txt ssh://10.0.0.5"},
        "reasons": ["credential_attack"],
    }
    fields.update(overrides)
    return ApprovalRequest(**fields)


async def test_create_persists_with_pending_defaults(db_session: AsyncSession) -> None:
    req = _new_request()
    db_session.add(req)
    await db_session.commit()

    stored = (await db_session.execute(select(ApprovalRequest))).scalar_one()
    assert stored.id is not None
    assert stored.status == "pending"  # server_default
    assert stored.created_at is not None
    # Pending rows have no decision attribution yet.
    assert stored.acted_by_user_id is None
    assert stored.self_approved is None
    assert stored.tool_run_id is None
    assert stored.decided_at is None
    assert stored.preset_name is None
    assert stored.rationale is None


async def test_json_columns_round_trip(db_session: AsyncSession) -> None:
    req = _new_request(
        args={"target": "10.0.0.5", "nested": {"flags": ["-A", "-T5"]}},
        reasons=["aggressive_scan", "target_write"],
        preset_name="aggressive",
        rationale="Aggressive scan to enumerate services.",
    )
    db_session.add(req)
    await db_session.commit()

    stored = (await db_session.execute(select(ApprovalRequest))).scalar_one()
    assert stored.args == {"target": "10.0.0.5", "nested": {"flags": ["-A", "-T5"]}}
    assert stored.reasons == ["aggressive_scan", "target_write"]
    assert stored.preset_name == "aggressive"
    assert stored.rationale == "Aggressive scan to enumerate services."


async def test_status_check_constraint_rejects_unknown(db_session: AsyncSession) -> None:
    db_session.add(_new_request(status="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize("status", APPROVAL_STATUSES)
async def test_status_check_constraint_allows_vocabulary(
    db_session: AsyncSession, status: str
) -> None:
    db_session.add(_new_request(status=status))
    await db_session.commit()  # no IntegrityError for any allowed status


def test_status_vocabulary_is_the_documented_set() -> None:
    assert APPROVAL_STATUSES == ("pending", "approved", "rejected")
