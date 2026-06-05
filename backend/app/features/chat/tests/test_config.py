"""Config tests for the chat feature's tunables (Slice 13, §5.3).

The autouse ``_settings_env`` fixture (conftest) provides the required env + clears the
``get_settings`` cache around each test, so these assert the chat-specific defaults load.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings


def test_low_confidence_threshold_defaults_to_70() -> None:
    """The §5.3 low-confidence threshold defaults to 70 (slice-13 Open Question 4)."""
    get_settings.cache_clear()
    assert get_settings().ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD == 70


def test_low_confidence_threshold_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator can tune the threshold via the environment (single place to tune)."""
    monkeypatch.setenv("ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD", "50")
    get_settings.cache_clear()
    try:
        assert get_settings().ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD == 50
    finally:
        get_settings.cache_clear()


def test_low_confidence_threshold_out_of_range_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The threshold is bounded 0–100 so a misconfiguration fails fast at load."""
    monkeypatch.setenv("ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD", "150")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError):
            get_settings()
    finally:
        get_settings.cache_clear()
