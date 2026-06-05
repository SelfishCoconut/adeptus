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


# ---------------------------------------------------------------------------
# Cloud LLM (Anthropic) settings — Slice 14 / §5.1 (Resolved decision 1)
# ---------------------------------------------------------------------------


def test_anthropic_api_key_defaults_to_none() -> None:
    """The cloud key is unset by default so a fresh instance is local-only/safe-by-default
    (§17.5); a cloud_enabled turn with no key fails rather than falling back (§5.1)."""
    get_settings.cache_clear()
    assert get_settings().ADEPTUS_ANTHROPIC_API_KEY is None


def test_anthropic_model_defaults_to_claude_sonnet() -> None:
    """The cloud model is pinned to claude-sonnet-4-6 (Resolved decision 1)."""
    get_settings.cache_clear()
    assert get_settings().ADEPTUS_ANTHROPIC_MODEL == "claude-sonnet-4-6"


def test_anthropic_base_url_defaults_to_public_api() -> None:
    """The cloud surface is the public Anthropic Messages API (Resolved decision 1)."""
    get_settings.cache_clear()
    assert get_settings().ADEPTUS_ANTHROPIC_BASE_URL == "https://api.anthropic.com"


def test_anthropic_settings_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Key/model/base-url are all env-overridable per instance (Resolved decision 1) so a
    self-host can point at a different model or a test transport without code change."""
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("ADEPTUS_ANTHROPIC_BASE_URL", "https://proxy.internal")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.ADEPTUS_ANTHROPIC_API_KEY == "sk-ant-test"
        assert settings.ADEPTUS_ANTHROPIC_MODEL == "claude-opus-4-8"
        assert settings.ADEPTUS_ANTHROPIC_BASE_URL == "https://proxy.internal"
    finally:
        get_settings.cache_clear()
