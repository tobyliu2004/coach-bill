"""Application configuration, loaded from the environment (and `.env` in dev)."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Typed app settings. Required values must be present or startup fails loudly."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase Postgres connection string (Session pooler URI). Required.
    database_url: str

    # Supabase project URL (https://<ref>.supabase.co) — where the JWKS endpoint lives,
    # so the backend can verify access tokens without holding any signing secret. Required.
    supabase_url: str

    # Allowed browser origins for CORS. Given in .env as a comma-separated string;
    # NoDecode stops pydantic-settings from trying to JSON-parse it first.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Build settings once and reuse. Cached so the .env is read a single time."""
    return Settings()  # type: ignore[call-arg]  # values come from env, not literals
