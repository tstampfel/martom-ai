"""Settings come from environment variables only, with safe local defaults."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from martom.olx import job
from martom.settings import Settings

ENV_VARS = [
    "APP_ENV",
    "LOG_LEVEL",
    "CORS_ALLOWED_ORIGINS",
    "DATABASE_URL",
    "REDIS_URL",
    "OLX_API_BASE_URL",
    "OLX_SHOP_USERNAME",
    "OLX_REQUEST_INTERVAL",
    "OLX_NIGHTLY_REQUEST_BUDGET",
    "OLX_ROLLING_REFRESH_DAYS",
    "CHAT_RETENTION_DAYS",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_point_at_the_local_compose_stack() -> None:
    settings = Settings()

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.database_url == "postgresql://martom:martom@localhost:5432/martom"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cors_allowed_origins == [
        "http://localhost:5173",
        "https://martom-savjetnik.pages.dev",
    ]


def test_olx_defaults_match_the_politeness_rule() -> None:
    settings = Settings()

    assert settings.olx_api_base_url == "https://api.olx.ba"
    assert settings.olx_shop_username == "martom"
    assert settings.olx_request_interval == 2.0


def test_rolling_refresh_defaults_match_the_sync_job() -> None:
    settings = Settings()

    assert settings.olx_nightly_request_budget == job.NIGHTLY_REQUEST_BUDGET == 950
    assert settings.olx_rolling_refresh_days == job.ROLLING_REFRESH_DAYS == 7


@pytest.mark.parametrize(
    ("name", "value"), [("OLX_NIGHTLY_REQUEST_BUDGET", "-1"), ("OLX_ROLLING_REFRESH_DAYS", "0")]
)
def test_rejects_a_negative_cap_or_zero_refresh_days(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


def test_chats_are_kept_90_days_by_default() -> None:
    # Tomislav, #129; the site's privacy notice promises 90 days.
    assert Settings().chat_retention_days == 90


@pytest.mark.parametrize("value", ["0", "-5"])
def test_rejects_a_chat_retention_below_one_day(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CHAT_RETENTION_DAYS", value)

    with pytest.raises(ValidationError):
        Settings()


def test_rejects_an_olx_interval_below_1_3_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLX_REQUEST_INTERVAL", "1.2")

    with pytest.raises(ValidationError):
        Settings()


def test_olx_interval_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLX_REQUEST_INTERVAL", "3.5")

    assert Settings().olx_request_interval == 3.5


def test_reads_values_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/martom")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")

    settings = Settings()

    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "postgresql://u:p@db:5432/martom"
    assert settings.redis_url == "redis://cache:6379/1"


def test_cors_origins_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        " https://martom-savjetnik.pages.dev , http://127.0.0.1:4173,",
    )

    assert Settings().cors_allowed_origins == [
        "https://martom-savjetnik.pages.dev",
        "http://127.0.0.1:4173",
    ]


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.com",
        "http://martom-savjetnik.pages.dev",  # pages.dev is HTTPS only
        "https://preview.martom-savjetnik.pages.dev",
        "*",
    ],
)
def test_rejects_origins_other_than_pages_dev_and_localhost(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origin)

    with pytest.raises(ValidationError, match="CORS"):
        Settings()


def test_rejects_unknown_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings()


def test_ignores_a_dotenv_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Config comes from env vars only; a stray .env in the working folder must not be read.
    (tmp_path / ".env").write_text("REDIS_URL=redis://from-dotenv:6379/0\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().redis_url == "redis://localhost:6379/0"


@pytest.mark.parametrize("name", ["DATABASE_URL", "REDIS_URL", "APP_ENV", "LOG_LEVEL"])
def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # Same as `martom migrate`, which treats DATABASE_URL="" as unset.
    monkeypatch.setenv(name, "")

    # model_construct() skips reading the environment, so it holds the plain defaults.
    defaults = Settings.model_construct()
    assert getattr(Settings(), name.lower()) == getattr(defaults, name.lower())
