"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

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
    jwt_secret: str = "dev-secret-change-in-production"
    access_token_expire_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
