"""App configuration, read from environment variables only.

`Settings()` reads each field from the env var of the same name in upper case
(`database_url` <- `DATABASE_URL`), validates and converts it, and falls back to the
default when the variable is unset. Think of it as a typed, validated `process.env`.
There is deliberately no `.env` file support: locally docker compose and your shell
provide the variables, in production ECS injects them from Secrets Manager.
"""

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# CORS may only ever allow the public customer site and local development servers.
PAGES_DEV_ORIGIN = "https://martom-savjetnik.pages.dev"
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?")


class Settings(BaseSettings):
    # env_file=None: never read a .env file, only real environment variables.
    # env_ignore_empty: VAR="" counts as unset (default used), like `martom migrate` does.
    model_config = SettingsConfigDict(env_file=None, env_ignore_empty=True, extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # NoDecode: take the raw string and split it ourselves (comma-separated), instead of
    # pydantic-settings' default of expecting a JSON list for list fields.
    cors_allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        PAGES_DEV_ORIGIN,
    ]
    # Fake local credentials, the same as .env.example and docker-compose.yml.
    database_url: str = "postgresql://martom:martom@localhost:5432/martom"
    redis_url: str = "redis://localhost:6379/0"

    # OLX.ba public API (no token). Read by `martom sync`.
    olx_api_base_url: str = "https://api.olx.ba"
    olx_shop_username: str = "martom"
    # Politeness rule: at most 1 request per 1.3 s; never configure it faster.
    olx_min_request_interval_seconds: float = Field(default=1.3, ge=1.3)

    # mode="before" runs on the raw env value, before pydantic checks the type.
    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _only_pages_dev_and_localhost(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin != PAGES_DEV_ORIGIN and not LOCAL_ORIGIN.fullmatch(origin):
                raise ValueError(
                    f"CORS origin {origin!r} is not allowed; use {PAGES_DEV_ORIGIN} "
                    "or http://localhost[:port]"
                )
        return origins
