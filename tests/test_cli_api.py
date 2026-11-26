"""`martom api` starts uvicorn with the app factory and JSON logging."""

import pytest
import uvicorn

from martom import cli
from martom.log import logging_config


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(app: str, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["APP_ENV", "LOG_LEVEL", "CORS_ALLOWED_ORIGINS"]:
        monkeypatch.delenv(name, raising=False)


def test_api_runs_the_app_factory_on_localhost_by_default(
    uvicorn_calls: list[dict[str, object]],
) -> None:
    assert cli.main(["api"]) == 0

    assert uvicorn_calls == [
        {
            "app": "martom.api.app:create_app",
            "factory": True,
            "host": "127.0.0.1",
            "port": 8000,
            "reload": False,
            "log_config": logging_config("INFO"),
        }
    ]


def test_api_accepts_host_port_and_reload(uvicorn_calls: list[dict[str, object]]) -> None:
    cli.main(["api", "--host", "0.0.0.0", "--port", "8080", "--reload"])

    assert uvicorn_calls[0]["host"] == "0.0.0.0"
    assert uvicorn_calls[0]["port"] == 8080
    assert uvicorn_calls[0]["reload"] is True


def test_api_passes_json_logging_at_the_settings_level(
    uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # uvicorn applies log_config in every process, including the --reload worker.
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    cli.main(["api"])

    assert uvicorn_calls[0]["log_config"] == logging_config("WARNING")


def test_api_fails_clearly_on_invalid_settings(
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://evil.example.com")

    assert cli.main(["api"]) == 1

    assert uvicorn_calls == []
    assert "CORS origin" in capsys.readouterr().err
