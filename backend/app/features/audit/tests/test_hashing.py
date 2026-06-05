"""Unit tests for the pure audit hashing helper (Slice 10 task 2, Risk 2).

These guard the load-bearing property that the hash is deterministic and changes
iff the content changes — the foundation of tamper-evidence (§14).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.features.audit import models
from app.features.audit.hashing import (
    GENESIS_HASH,
    AuditContent,
    canonical_bytes,
    compute_entry_hash,
)

_TS = datetime(2026, 6, 5, 12, 34, 56, 789012, tzinfo=UTC)


def _content(**overrides: object) -> AuditContent:
    base: dict[str, object] = {
        "seq": 1,
        "created_at": _TS,
        "action": "login",
        "actor_user_id": uuid4(),
        "engagement_id": None,
        "target_type": None,
        "target_id": None,
        "self_approved": None,
        "payload": {},
    }
    base.update(overrides)
    return AuditContent(**base)  # type: ignore[arg-type]


def test_hash_is_deterministic() -> None:
    content = _content()
    assert compute_entry_hash(GENESIS_HASH, content) == compute_entry_hash(GENESIS_HASH, content)


def test_hash_is_64_hex_lowercase() -> None:
    h = compute_entry_hash(GENESIS_HASH, _content())
    assert len(h) == 64
    assert h == h.lower()
    bytes.fromhex(h)  # does not raise => valid hex


def test_hash_changes_when_action_changes() -> None:
    c1 = _content(action="login")
    c2 = _content(action="logout", actor_user_id=c1.actor_user_id)
    assert compute_entry_hash(GENESIS_HASH, c1) != compute_entry_hash(GENESIS_HASH, c2)


def test_hash_changes_when_actor_changes() -> None:
    c1 = _content(actor_user_id=uuid4())
    c2 = _content(actor_user_id=uuid4())
    assert compute_entry_hash(GENESIS_HASH, c1) != compute_entry_hash(GENESIS_HASH, c2)


def test_hash_changes_when_seq_changes() -> None:
    actor = uuid4()
    c1 = _content(seq=1, actor_user_id=actor)
    c2 = _content(seq=2, actor_user_id=actor)
    assert compute_entry_hash(GENESIS_HASH, c1) != compute_entry_hash(GENESIS_HASH, c2)


def test_hash_changes_when_payload_changes() -> None:
    actor = uuid4()
    c1 = _content(payload={"a": 1}, actor_user_id=actor)
    c2 = _content(payload={"a": 2}, actor_user_id=actor)
    assert compute_entry_hash(GENESIS_HASH, c1) != compute_entry_hash(GENESIS_HASH, c2)


def test_hash_changes_when_prev_hash_changes() -> None:
    content = _content()
    other = "1" * 64
    assert compute_entry_hash(GENESIS_HASH, content) != compute_entry_hash(other, content)


def test_payload_key_order_does_not_change_hash() -> None:
    actor = uuid4()
    c1 = _content(payload={"alpha": 1, "beta": {"x": 1, "y": 2}}, actor_user_id=actor)
    c2 = _content(payload={"beta": {"y": 2, "x": 1}, "alpha": 1}, actor_user_id=actor)
    assert compute_entry_hash(GENESIS_HASH, c1) == compute_entry_hash(GENESIS_HASH, c2)


def test_timestamp_precision_is_fixed() -> None:
    # microseconds == 0 must still serialize the .000000 fraction (no isoformat drop).
    content = _content(created_at=datetime(2026, 6, 5, 0, 0, 0, tzinfo=UTC))
    assert b"00:00:00.000000Z" in canonical_bytes(content)


def test_naive_timestamp_treated_as_utc() -> None:
    actor = uuid4()
    aware = _content(actor_user_id=actor, created_at=datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC))
    naive = _content(actor_user_id=actor, created_at=datetime(2026, 6, 5, 12, 0, 0))  # noqa: DTZ001
    assert canonical_bytes(aware) == canonical_bytes(naive)


def test_self_approved_true_false_none_all_differ() -> None:
    actor = uuid4()
    h_true = compute_entry_hash(GENESIS_HASH, _content(self_approved=True, actor_user_id=actor))
    h_false = compute_entry_hash(GENESIS_HASH, _content(self_approved=False, actor_user_id=actor))
    h_none = compute_entry_hash(GENESIS_HASH, _content(self_approved=None, actor_user_id=actor))
    assert len({h_true, h_false, h_none}) == 3


def test_none_actor_differs_from_present_actor() -> None:
    h_none = compute_entry_hash(GENESIS_HASH, _content(actor_user_id=None))
    h_some = compute_entry_hash(GENESIS_HASH, _content(actor_user_id=uuid4()))
    assert h_none != h_some


def test_genesis_prev_hash_is_64_zeros() -> None:
    assert GENESIS_HASH == "0" * 64
    # Genesis hashing must succeed and decode the zeros to 32 zero bytes.
    assert compute_entry_hash(GENESIS_HASH, _content())  # no raise


def test_genesis_constant_matches_models() -> None:
    assert GENESIS_HASH == models.GENESIS_HASH


def test_writer_and_verifier_recompute_matches() -> None:
    # Pure round-trip: the verifier recomputing the same content reproduces the hash.
    content = _content(
        action="graph_node_created",
        engagement_id=uuid4(),
        target_type="node",
        target_id=str(uuid4()),
        payload={"label": "10.0.0.5", "type": "host"},
    )
    written = compute_entry_hash(GENESIS_HASH, content)
    recomputed = compute_entry_hash(GENESIS_HASH, content)
    assert written == recomputed


@pytest.mark.parametrize("bad", ["", "abc", "0" * 63, "0" * 65, "z" * 64])
def test_invalid_prev_hash_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        compute_entry_hash(bad, _content())
