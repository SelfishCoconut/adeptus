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


@lru_cache
def get_settings() -> Settings:
    return Settings()
