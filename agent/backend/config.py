"""Backend settings, all sourced from the environment (never hard-coded).

Mirrors the existing repo convention (env-var driven, `.env` via docker-compose)
but centralizes everything the backend needs in one typed object instead of
scattered `os.environ.get(...)` calls.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---------------------------------------------------------
    # Async SQLAlchemy URL. Postgres in docker-compose/prod
    # ("postgresql+asyncpg://..."), SQLite in tests ("sqlite+aiosqlite:///...").
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    # --- Auth / JWT ---------------------------------------------------------
    jwt_secret_key: str = "dev-secret-change-me"          # MUST be overridden outside dev
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # The refresh cookie's Secure flag. MUST be true whenever the app is served
    # over HTTPS (browsers won't send a Secure cookie back over plain HTTP at
    # all, so leave this false only for local/plain-HTTP dev — e.g. the default
    # docker-compose stack, which terminates no TLS itself).
    cookie_secure: bool = False

    # Symmetric key (Fernet, 32 url-safe base64 bytes) used to encrypt LLM
    # provider API keys at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = "PSbEP1cKk-C1WPz2xCXQfxA1qk4y1jf6VqvKz2r9x0o="  # dev-only default

    # --- Bootstrap admin (created once, only if the users table is empty) --
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me-now"

    # --- CORS ---------------------------------------------------------------
    # Comma-separated list of allowed origins. Never "*" — the API uses
    # cookie-based refresh tokens, which requires allow_credentials=True, and
    # browsers reject a wildcard origin alongside credentials anyway.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # --- MCP servers (seed set; DB rows are the persisted source after boot) -
    mcp_server_urls: str = ""

    # --- LLM provider keys (env fallback for the seed LLM registry) --------
    llm_provider: str = "mock"
    llm_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    mistral_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
