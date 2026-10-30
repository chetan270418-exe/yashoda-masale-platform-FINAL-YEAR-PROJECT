"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Yashoda SpiceChain AI"
    environment: str = "local"
    database_url: str = (
        "postgresql+psycopg://yashoda_dev:local_dev_only@localhost:5432/yashoda_masale"
    )
    cors_origins: str = "http://localhost:3000"

    # Auth / JWT
    jwt_secret: str = "dev-secret-change-in-production-key-32"
    access_token_expire_minutes: int = 30

    # Rate limiting & Proxy
    login_rate_limit_max: int = 5
    login_rate_limit_window_seconds: int = 60
    trust_proxy_headers: bool = False

    # LLM provider (Decision D-010). The API key lives only in the environment,
    # never in the repository and never in the frontend (Rule 33). Leaving the
    # provider empty or "mock" keeps the app on the deterministic mock adapter
    # so it runs offline and in CI (Rule 72).
    llm_provider: str = "mock"
    llm_api_key: str = ""
    # Only needed to override a provider's default base URL, or to point the
    # adapter at a self-hosted or proxied endpoint.
    llm_base_url: str = ""
    # Optional. Defaults to a sensible model for the chosen provider.
    llm_model: str = ""
    llm_timeout_seconds: int = 15

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Refuse in non-local environments if JWT_SECRET is empty or uses dev default."""
        if self.environment != "local":
            dev_secret = "dev-secret-change-in-production-key-32"
            if not self.jwt_secret or self.jwt_secret == dev_secret:
                raise ValueError(
                    "JWT_SECRET must be set and cannot be the default value "
                    "outside the local environment."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
