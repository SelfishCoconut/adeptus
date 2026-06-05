from functools import lru_cache

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

    # DEV/TEST ONLY — ignored when ENVIRONMENT=production (see auth/service.py bootstrap_test_user)
    ADEPTUS_TEST_USER_USERNAME: str | None = None
    ADEPTUS_TEST_USER_PASSWORD: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
