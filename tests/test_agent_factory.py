from typing import Any

from pydantic import SecretStr

from martom.agent.factory import make_agent_models
from martom.settings import Settings


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_no_api_key_means_no_advisor() -> None:
    assert make_agent_models(settings(anthropic_api_key=None)) is None
    assert make_agent_models(settings(anthropic_api_key=SecretStr("  "))) is None


def test_models_use_the_configured_ids_without_calling_the_api() -> None:
    models = make_agent_models(
        settings(anthropic_api_key=SecretStr("sk-ant-fake"), agent_model="claude-sonnet-5-5")
    )
    assert models is not None
    assert models.chat_model_name == "claude-sonnet-5-5"
    assert models.guard_model_name == "claude-haiku-5-5"
    assert models.chat.model == "claude-sonnet-5-5"  # type: ignore[attr-defined]
