"""Schema validation tests: content bounds, enum/DB-vocabulary parity."""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.features.chat import models
from app.features.chat.schemas import (
    MAX_MESSAGE_CHARS,
    ChatMessageCreate,
    ChatMessageRead,
    ChatMessageStatus,
    ChatRole,
    ChatTurnDebug,
    Claim,
    EgressConfirmationRequired,
    EgressRefusalReason,
    GraphSubsetEdge,
    GraphSubsetNode,
    GraphSubsetReason,
    PlanStep,
    PlanStepStatus,
)


def test_role_enum_matches_db_vocabulary() -> None:
    """ChatRole must mirror models.CHAT_ROLES exactly (no silent drift)."""
    assert {r.value for r in ChatRole} == set(models.CHAT_ROLES)


def test_status_enum_matches_db_vocabulary() -> None:
    """ChatMessageStatus must mirror models.CHAT_STATUSES exactly."""
    assert {s.value for s in ChatMessageStatus} == set(models.CHAT_STATUSES)


def test_empty_content_rejected() -> None:
    """A user message must carry at least one character (min_length=1)."""
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="")


def test_content_over_max_rejected() -> None:
    """Content longer than MAX_MESSAGE_CHARS is rejected."""
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="x" * (MAX_MESSAGE_CHARS + 1))


def test_confirmed_egress_defaults_false() -> None:
    """The Slice-14 egress-confirmation flag defaults False (friction is opt-out-by-acknowledge)."""
    assert ChatMessageCreate(content="hi").confirmed_egress is False


def test_confirmed_egress_accepts_true() -> None:
    """A client that saw the modal sends confirmed_egress=true (§5.1)."""
    assert ChatMessageCreate(content="hi", confirmed_egress=True).confirmed_egress is True


def test_egress_confirmation_required_round_trips() -> None:
    """EgressConfirmationRequired round-trips with reason + category NAMES (never values, §5.5)."""
    body = EgressConfirmationRequired(
        reason=EgressRefusalReason.EGRESS_SECRET_FLAGGED,
        matched_categories=["aws_access_key", "password_assignment"],
    )
    again = EgressConfirmationRequired.model_validate(body.model_dump())
    assert again == body
    assert again.reason == EgressRefusalReason.EGRESS_SECRET_FLAGGED
    assert again.matched_categories == ["aws_access_key", "password_assignment"]


def test_egress_confirmation_required_categories_default_empty() -> None:
    """The archived reason carries no categories — the list defaults empty."""
    body = EgressConfirmationRequired(reason=EgressRefusalReason.ENGAGEMENT_ARCHIVED)
    assert body.matched_categories == []


def test_content_at_max_accepted() -> None:
    """Content exactly at the limit is accepted and passed through unchanged (§5.5)."""
    text = "x" * MAX_MESSAGE_CHARS
    assert ChatMessageCreate(content=text).content == text


def test_content_passes_through_verbatim() -> None:
    """No redaction/normalization on the way in (§5.5) — value is byte-for-byte intact,
    even for sensitive-looking content (the model needs full context to be useful)."""
    raw = "  creds for box-7 are <not-redacted-here>\n\ttrailing-whitespace-kept  "
    assert ChatMessageCreate(content=raw).content == raw


def test_node_id_lists_default_empty() -> None:
    """The three §5.3 union id lists are optional and default to empty lists."""
    body = ChatMessageCreate(content="hi")
    assert body.pinned_node_ids == []
    assert body.recent_node_ids == []
    assert body.mentioned_node_ids == []


def test_node_id_lists_accept_uuids() -> None:
    """Each id list coerces well-formed UUID strings (the JSON wire form) into UUIDs."""
    a, b = uuid4(), uuid4()
    body = ChatMessageCreate.model_validate(
        {
            "content": "hi",
            "pinned_node_ids": [str(a)],
            "recent_node_ids": [str(b)],
            "mentioned_node_ids": [str(a), str(b)],
        }
    )
    assert body.pinned_node_ids == [a]
    assert body.recent_node_ids == [b]
    assert body.mentioned_node_ids == [a, b]


def test_invalid_node_id_rejected() -> None:
    """A malformed id in any union list is a validation error (server never trusts it)."""
    with pytest.raises(ValidationError):
        ChatMessageCreate.model_validate({"content": "hi", "pinned_node_ids": ["not-a-uuid"]})


def test_subset_reason_enum_values() -> None:
    """The inclusion-reason vocabulary matches the four §5.3 union arms."""
    assert {r.value for r in GraphSubsetReason} == {"pinned", "recent", "mentioned", "keyword"}


def test_chat_turn_debug_round_trips() -> None:
    """ChatTurnDebug serializes and re-validates without loss (debug-panel contract)."""
    node_id, src, tgt, edge_id, msg_id = (uuid4() for _ in range(5))
    debug = ChatTurnDebug(
        message_id=msg_id,
        model="qwen3.5:9b",
        status=ChatMessageStatus.COMPLETE,
        nodes=[
            GraphSubsetNode(
                id=node_id,
                type="endpoint",
                label="/login",
                reasons=[GraphSubsetReason.PINNED, GraphSubsetReason.KEYWORD],
            )
        ],
        edges=[GraphSubsetEdge(id=edge_id, source_id=src, target_id=tgt, relation="hosts")],
        context_block="## Graph context\n- endpoint /login",
        raw_prompt="system...\nuser...",
        model_output="Try default creds.",
    )
    again = ChatTurnDebug.model_validate(debug.model_dump())
    assert again == debug
    assert again.nodes[0].reasons == [GraphSubsetReason.PINNED, GraphSubsetReason.KEYWORD]


# ---------------------------------------------------------------------------
# Slice 13 — visible plan + certainty signaling (§5.3)
# ---------------------------------------------------------------------------


def test_plan_step_status_enum_values() -> None:
    """The plan-step status vocabulary is exactly the three §5.3 lifecycle states."""
    assert {s.value for s in PlanStepStatus} == {"todo", "in_progress", "done"}


def test_plan_step_requires_step_and_status() -> None:
    """A PlanStep round-trips its verbatim text + status (no redaction, §5.5)."""
    step = PlanStep(step="Test SQLi on /login", status=PlanStepStatus.IN_PROGRESS)
    assert step.step == "Test SQLi on /login"
    assert step.status == PlanStepStatus.IN_PROGRESS
    assert PlanStep.model_validate(step.model_dump()) == step


def test_claim_certainty_below_zero_rejected() -> None:
    """A negative certainty is out of the 0–100 contract (parser clamps before construct)."""
    with pytest.raises(ValidationError):
        Claim(text="maybe", certainty=-1)


def test_claim_certainty_above_hundred_rejected() -> None:
    """A certainty over 100 is out of the 0–100 contract."""
    with pytest.raises(ValidationError):
        Claim(text="surely", certainty=101)


def test_claim_certainty_bounds_accepted() -> None:
    """The inclusive bounds 0 and 100 are valid certainties."""
    assert Claim(text="no idea", certainty=0).certainty == 0
    assert Claim(text="certain", certainty=100).certainty == 100


def test_claim_node_id_defaults_none() -> None:
    """A claim with no graph reference carries a null node_id (no Graph-pane badge)."""
    claim = Claim(text="HTTPS uses port 443", certainty=99)
    assert claim.node_id is None


def test_claim_node_id_accepts_uuid() -> None:
    """A claim about a known node coerces the wire UUID string into a UUID."""
    nid = uuid4()
    claim = Claim.model_validate({"text": "likely Apache", "certainty": 60, "node_id": str(nid)})
    assert claim.node_id == nid


def test_chat_message_read_defaults_plan_and_claims_empty() -> None:
    """A read row with no plan/claims (user/pending/pre-slice) defaults both to empty."""
    msg = ChatMessageRead(
        id=uuid4(),
        engagement_id=uuid4(),
        role=ChatRole.USER,
        content="hi",
        status=ChatMessageStatus.COMPLETE,
        created_at=datetime(2026, 1, 1),
    )
    assert msg.plan == []
    assert msg.claims == []


def test_chat_message_read_round_trips_with_plan_and_claims() -> None:
    """An assistant read row carries its parsed plan + claims through serialization."""
    nid = uuid4()
    msg = ChatMessageRead(
        id=uuid4(),
        engagement_id=uuid4(),
        role=ChatRole.ASSISTANT,
        content="Try default creds.",
        status=ChatMessageStatus.COMPLETE,
        created_at=datetime(2026, 1, 1),
        plan=[PlanStep(step="Enumerate login", status=PlanStepStatus.DONE)],
        claims=[Claim(text="likely Apache 2.4", certainty=60, node_id=nid)],
    )
    again = ChatMessageRead.model_validate(msg.model_dump())
    assert again == msg
    assert again.plan[0].status == PlanStepStatus.DONE
    assert again.claims[0].node_id == nid


def test_persona_id_defaults_none_on_create() -> None:
    """An absent persona_id defaults to None (server resolves it to general, Slice 15)."""
    assert ChatMessageCreate(content="hi").persona_id is None


def test_persona_id_accepts_uuid_on_create() -> None:
    pid = uuid4()
    assert ChatMessageCreate(content="hi", persona_id=pid).persona_id == pid


def test_chat_message_read_defaults_persona_fields_none() -> None:
    """A read row with no persona (user/pre-slice) defaults both persona fields to None."""
    msg = ChatMessageRead(
        id=uuid4(),
        engagement_id=uuid4(),
        role=ChatRole.USER,
        content="hi",
        status=ChatMessageStatus.COMPLETE,
        created_at=datetime(2026, 1, 1),
    )
    assert msg.persona_id is None
    assert msg.persona_name is None


def test_chat_message_read_round_trips_persona_fields() -> None:
    """An assistant read row carries its persona id + name through serialization."""
    pid = uuid4()
    msg = ChatMessageRead(
        id=uuid4(),
        engagement_id=uuid4(),
        role=ChatRole.ASSISTANT,
        content="recon answer",
        status=ChatMessageStatus.COMPLETE,
        created_at=datetime(2026, 1, 1),
        persona_id=pid,
        persona_name="Recon",
    )
    again = ChatMessageRead.model_validate(msg.model_dump())
    assert again.persona_id == pid
    assert again.persona_name == "Recon"


def test_chat_turn_debug_defaults_plan_and_claims_empty() -> None:
    """ChatTurnDebug plan/claims default empty so a pre-slice turn validates."""
    debug = ChatTurnDebug(
        message_id=uuid4(),
        model="qwen3.5:9b",
        status=ChatMessageStatus.COMPLETE,
        nodes=[],
        edges=[],
        context_block="",
        raw_prompt="",
        model_output="",
    )
    assert debug.plan == []
    assert debug.claims == []
