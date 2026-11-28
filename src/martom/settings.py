"""App configuration, read from environment variables only.

`Settings()` reads each field from the env var of the same name in upper case
(`database_url` <- `DATABASE_URL`), validates and converts it, and falls back to the
default when the variable is unset. Think of it as a typed, validated `process.env`.
There is deliberately no `.env` file support: locally docker compose and your shell
provide the variables, in production ECS injects them from Secrets Manager.
"""

import re
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# CORS may only ever allow the public customer site and local development servers.
PAGES_DEV_ORIGIN = "https://martom-savjetnik.pages.dev"
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?")


class Settings(BaseSettings):
    # env_file=None: never read a .env file, only real environment variables.
    # env_ignore_empty: VAR="" counts as unset (default used), like `martom migrate` does.
    # hide_input_in_errors: a validation error names the field and the rule but never the
    # value, so a mis-set secret (ADMIN_TOKEN, ANTHROPIC_API_KEY) can't end up on stderr or
    # in CloudWatch through the "invalid settings" message.
    model_config = SettingsConfigDict(
        env_file=None, env_ignore_empty=True, extra="ignore", hide_input_in_errors=True
    )

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
    # Search cache in Redis (M3-5, search/cache.py). Off: every search pays for its Haiku
    # parse, embedding and rerank. Results also carry the catalogue version in their key,
    # so a sync, extraction or embed run makes them misses at once; the TTL bounds how
    # long anything derived from a customer's question stays in Redis.
    search_cache_enabled: bool = True
    search_cache_ttl_seconds: int = Field(default=3600, ge=1)  # whole retrieve() results
    query_cache_ttl_seconds: int = Field(default=86400, ge=1)  # parsed filters, query vectors

    # OLX.ba public API (no token). Read by `martom sync`.
    olx_api_base_url: str = "https://api.olx.ba"
    olx_shop_username: str = "martom"
    # Seconds between OLX request starts. Politeness rule: 2.0 s by default, never
    # configured below 1.3 s; after a 429 the client raises it for the rest of the run.
    olx_request_interval: float = Field(default=2.0, ge=1.3)

    # Rolling refresh of the nightly `martom sync` (#135). Each night it re-fetches the
    # details of ceil(active / OLX_ROLLING_REFRESH_DAYS) listings, oldest fetch first, so
    # every active listing is refreshed within that many nights. OLX_NIGHTLY_REQUEST_BUDGET
    # caps the night's OLX requests of every kind (OLX allows 1,000 per fixed hourly
    # window, #105); everything else is always sent, the slice gets what is left.
    # Defaults match NIGHTLY_REQUEST_BUDGET and ROLLING_REFRESH_DAYS in
    # src/martom/olx/job.py. `--full` ignores both.
    olx_nightly_request_budget: int = Field(default=950, ge=0)
    olx_rolling_refresh_days: int = Field(default=7, ge=1)

    # Claude, read by `martom extract` (M2-1). SecretStr keeps the key out of logs and
    # reprs (it prints as '**********'); None means "not configured".
    anthropic_api_key: SecretStr | None = None
    # Sonnet for the advisor's answers and tool calls (M4-3, agent/factory.py).
    agent_model: str = "claude-sonnet-5-5"
    # Haiku for extraction, query parsing and grading (CLAUDE.md, Stack).
    fast_model: str = "claude-haiku-5-5"
    # Listings extracted at the same time (one Claude call each).
    extraction_concurrency: int = Field(default=4, ge=1, le=16)

    # Embeddings for hybrid search (M3-3), read by `martom embed` and search. "cohere" (the
    # default, chosen in M3-3) calls Cohere's API and needs COHERE_API_KEY; without it
    # `martom embed` refuses to run. "local" runs an open sentence-transformers model on this
    # machine (`uv sync --group local-embeddings`); "fake" gives meaningless deterministic
    # vectors, for trying the commands locally.
    embedding_provider: Literal["cohere", "local", "fake"] = "cohere"
    # The provider's model; None = the provider's default (src/martom/embeddings.py).
    embedding_model: str | None = None
    cohere_api_key: SecretStr | None = None
    # Cohere rerank model for search (M3-4), using COHERE_API_KEY. Without the key, results
    # stay in hybrid (RRF) order. Default: Rerank 5 Fast, which tied Rerank 4 Fast on the eval
    # set (#16) and is newer and cheaper; "rerank-v4.0-fast" stays selectable.
    rerank_model: str = "rerank-v5.0-fast"

    # Bearer token for the /admin endpoints (M2-3), e.g. GET /admin/review-issues.
    # Unset (the default) turns them off: they answer 404, never open. The API checks the
    # 32-character minimum at startup (`check_admin_token`), not this class, so a short token
    # never stops commands that don't serve /admin (sync, extract, scripts).
    admin_token: SecretStr | None = None

    # Days a customer chat (a checkpointer thread) is kept after its last message; the
    # nightly `martom purge-chats` deletes older ones (Tomislav, #129: 90 days). The site's
    # privacy notice promises 90 days, so never raise it without changing that text.
    chat_retention_days: int = Field(default=90, ge=1)

    # Password of Umami's database role (#163), read only by `martom umami-db-setup`, the
    # one-off task that creates the role (infra/umami.tf). Never logged or printed; the
    # task sends only a SCRAM verifier of it to Postgres (src/martom/umami_db.py).
    umami_db_password: SecretStr | None = None

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
