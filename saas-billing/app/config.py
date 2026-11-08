"""Typed application settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SaaS Billing & Subscription Engine"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"

    # Required: the app refuses to start without them (fail fast, no insecure defaults).
    database_url: str
    secret_key: SecretStr

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    # Convenient for a university project; in production use Alembic migrations instead.
    auto_create_tables: bool = False

    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "saas-billing-engine"
    access_token_expire_minutes: int = 30

    billing_period_days: int = 30

    @field_validator("secret_key")
    @classmethod
    def _secret_long_enough(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
