from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    ADEPTUS_ADMIN_USER: str
    ADEPTUS_ADMIN_PASSWORD_HASH: str

    SESSION_COOKIE_NAME: str = "session_id"
    SESSION_TTL_DAYS: int = 14
    ENVIRONMENT: str = "production"

    # Local LLM (Ollama) — Slice 11 / ADR-0004. The local-first chat path POSTs to
    # ADEPTUS_OLLAMA_URL/api/chat with the default model below (configurable per deploy).
    ADEPTUS_OLLAMA_URL: str = "http://ollama:11434"
    ADEPTUS_LLM_MODEL: str = "qwen3.5:9b"

    # Slice 12 — §5.3 "relevant subset" caps. The recent ("last N nodes touched in the
    # conversation") and mentioned ("nodes @-mentioned in the last K messages") union arms
    # are truncated to these. There is deliberately NO token budget (planning Decision 3):
    # the assembled subset is sent to the model in full and verbatim.
    ADEPTUS_GRAPH_CONTEXT_RECENT_LIMIT: int = 15
    ADEPTUS_GRAPH_CONTEXT_MENTIONED_LIMIT: int = 10

    # Slice 13 — §5.3 uncertainty signaling. A parsed claim whose certainty is BELOW this
    # percentage is rendered as low-confidence (amber) in chat + on graph items. Canonical
    # single place to tune; the frontend mirrors this default (no schema/endpoint change in
    # this slice — see slice-13 Open Question 4).
    ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD: int = Field(default=70, ge=0, le=100)

    # DEV/TEST ONLY — ignored when ENVIRONMENT=production (see auth/service.py bootstrap_test_user)
    ADEPTUS_TEST_USER_USERNAME: str | None = None
    ADEPTUS_TEST_USER_PASSWORD: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
