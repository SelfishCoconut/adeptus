"""Unit tests for engagements Pydantic schemas.

Covers field presence, enum membership, and validation constraints for
schemas introduced in Slice 06 (kill switches / pause).
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.features.engagements.schemas import (
    EngagementDetail,
    EngagementPauseRequest,
    EngagementPauseState,
    EngagementSummary,
)

# ---------------------------------------------------------------------------
# EngagementSummary — paused field (Slice 06)
# ---------------------------------------------------------------------------


class TestEngagementSummaryPaused:
    """EngagementSummary now includes a 'paused' boolean field."""

    def _make(self, **overrides: object) -> dict[str, object]:
        from datetime import UTC, datetime

        return {
            "id": str(uuid4()),
            "name": "Test Engagement",
            "status": "active",
            "created_at": datetime.now(tz=UTC),
            "member_role": "owner",
            "privacy_mode": "local_only",
            **overrides,
        }

    def test_paused_defaults_to_false(self) -> None:
        summary = EngagementSummary.model_validate(self._make())
        assert summary.paused is False

    def test_paused_true_accepted(self) -> None:
        summary = EngagementSummary.model_validate(self._make(paused=True))
        assert summary.paused is True

    def test_paused_false_explicit(self) -> None:
        summary = EngagementSummary.model_validate(self._make(paused=False))
        assert summary.paused is False

    def test_invalid_paused_type_rejected(self) -> None:
        # Pydantic v2 is lenient with bool coercion from strings like "yes"/"no",
        # but rejects non-bool-like objects such as a list.
        with pytest.raises(ValidationError):
            EngagementSummary.model_validate(self._make(paused=["not", "a", "bool"]))


# ---------------------------------------------------------------------------
# EngagementDetail — paused field (Slice 06)
# ---------------------------------------------------------------------------


class TestEngagementDetailPaused:
    """EngagementDetail now includes a 'paused' boolean field."""

    def _make(self, **overrides: object) -> dict[str, object]:
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        return {
            "id": str(uuid4()),
            "name": "Test Engagement",
            "status": "active",
            "scope": "*.example.com",
            "client_info": None,
            "created_at": now,
            "updated_at": now,
            "member_role": "owner",
            "privacy_mode": "local_only",
            "concurrency_slot_limit": 3,
            **overrides,
        }

    def test_paused_defaults_to_false(self) -> None:
        detail = EngagementDetail.model_validate(self._make())
        assert detail.paused is False

    def test_paused_true_accepted(self) -> None:
        detail = EngagementDetail.model_validate(self._make(paused=True))
        assert detail.paused is True

    def test_paused_false_explicit(self) -> None:
        detail = EngagementDetail.model_validate(self._make(paused=False))
        assert detail.paused is False

    def test_invalid_paused_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EngagementDetail.model_validate(self._make(paused=["not", "a", "bool"]))


# ---------------------------------------------------------------------------
# EngagementPauseRequest — Slice 06
# ---------------------------------------------------------------------------


class TestEngagementPauseRequest:
    """EngagementPauseRequest: required 'paused' boolean."""

    def test_paused_true_accepted(self) -> None:
        req = EngagementPauseRequest(paused=True)
        assert req.paused is True

    def test_paused_false_accepted(self) -> None:
        req = EngagementPauseRequest(paused=False)
        assert req.paused is False

    def test_missing_paused_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EngagementPauseRequest.model_validate({})

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EngagementPauseRequest.model_validate({"paused": {"not": "a bool"}})

    def test_model_validate_from_dict(self) -> None:
        req = EngagementPauseRequest.model_validate({"paused": True})
        assert req.paused is True


# ---------------------------------------------------------------------------
# EngagementPauseState — Slice 06
# ---------------------------------------------------------------------------


class TestEngagementPauseState:
    """EngagementPauseState: response shape after pause/resume action."""

    def _make(self, **overrides: object) -> dict[str, object]:
        return {
            "engagement_id": str(uuid4()),
            "paused": True,
            "killed_running": 0,
            "dequeued": 0,
            **overrides,
        }

    def test_valid_pause_state(self) -> None:
        state = EngagementPauseState.model_validate(self._make())
        assert state.paused is True
        assert state.killed_running == 0
        assert state.dequeued == 0

    def test_engagement_id_is_uuid(self) -> None:
        state = EngagementPauseState.model_validate(self._make())
        assert isinstance(state.engagement_id, UUID)

    def test_killed_running_and_dequeued_nonzero(self) -> None:
        state = EngagementPauseState.model_validate(self._make(killed_running=3, dequeued=2))
        assert state.killed_running == 3
        assert state.dequeued == 2

    def test_paused_false_for_resume(self) -> None:
        state = EngagementPauseState.model_validate(
            self._make(paused=False, killed_running=0, dequeued=0)
        )
        assert state.paused is False

    def test_missing_required_field_rejected(self) -> None:
        data = self._make()
        del data["killed_running"]
        with pytest.raises(ValidationError):
            EngagementPauseState.model_validate(data)

    def test_missing_engagement_id_rejected(self) -> None:
        data = self._make()
        del data["engagement_id"]
        with pytest.raises(ValidationError):
            EngagementPauseState.model_validate(data)

    def test_invalid_engagement_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EngagementPauseState.model_validate(self._make(engagement_id="not-a-uuid"))
