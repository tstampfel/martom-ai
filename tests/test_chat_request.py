"""The POST /api/chat request body: side lengths travel as numbers plus a closed flag."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from martom.api.chat_request import ChatRequest, ClarifyAnswers, chat_request_schema

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ROOT / "docs" / "api" / "chat-request.schema.json"
DOCS_PATH = ROOT / "docs" / "api" / "chat-events.md"


def answers(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": {"posts": "plate", "color": "antracit"},
        "sides": [10, 5, 10],
        "closed": False,
    }
    body.update(overrides)
    return body


def test_first_message_has_no_thread_and_no_answers() -> None:
    request = ChatRequest.model_validate(
        {"message": "Treba mi 25 m ograde", "thread_id": None, "answers": None}
    )
    assert request.answers is None


def test_clarify_answers_carry_the_sides_as_numbers() -> None:
    request = ChatRequest.model_validate(
        {"message": "2 kuta · 10 + 5 + 10 m", "thread_id": "t-1", "answers": answers()}
    )
    assert request.answers is not None
    assert request.answers.sides == [10, 5, 10]
    assert request.answers.choices == {"posts": "plate", "color": "antracit"}


def test_sides_as_a_string_are_rejected() -> None:
    # "10 + 5 + 10" is display text only; the agent never parses it.
    with pytest.raises(ValidationError):
        ClarifyAnswers.model_validate(answers(sides="10 + 5 + 10"))


@pytest.mark.parametrize("side", [0, -1, 300.01, 1000])
def test_each_side_is_above_0_and_at_most_300_m(side: float) -> None:
    with pytest.raises(ValidationError, match="sides"):
        ClarifyAnswers.model_validate(answers(sides=[10, side]))


@pytest.mark.parametrize("side", [0.5, 300])
def test_side_limits_are_inclusive_at_300(side: float) -> None:
    assert ClarifyAnswers.model_validate(answers(sides=[side])).sides == [side]


@pytest.mark.parametrize(
    ("sides", "closed", "corners"),
    [
        ([25], False, 0),
        ([10, 5, 10], False, 2),
        ([10, 5, 10, 5], True, 4),
        ([10, 10, 10], True, 3),
        ([], False, None),
    ],
)
def test_corners_follow_from_the_sides_and_the_closed_flag(
    sides: list[float], closed: bool, corners: int | None
) -> None:
    assert ClarifyAnswers.model_validate(answers(sides=sides, closed=closed)).corners == corners


@pytest.mark.parametrize("sides", [[], [10], [10, 5]])
def test_a_closed_fence_needs_at_least_three_sides(sides: list[float]) -> None:
    with pytest.raises(ValidationError, match="closed"):
        ClarifyAnswers.model_validate(answers(sides=sides, closed=True))


def test_closed_must_be_a_real_boolean() -> None:
    with pytest.raises(ValidationError):
        ClarifyAnswers.model_validate(answers(closed="yes"))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ClarifyAnswers.model_validate(answers(corners=2))
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"message": "x", "thread_id": None, "answers": None, "x": 1})


def test_message_is_not_empty() -> None:
    with pytest.raises(ValidationError, match="message"):
        ChatRequest.model_validate({"message": "", "thread_id": None, "answers": None})


def test_committed_json_schema_is_up_to_date() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == chat_request_schema(), (
        "docs/api/chat-request.schema.json is out of date: "
        "run `uv run python -m martom.api.chat_events` and commit the result"
    )


def test_every_docs_request_example_is_valid() -> None:
    text = DOCS_PATH.read_text(encoding="utf-8")
    request_part = text.split("\n## Request\n")[1].split("\n## Events\n")[0]
    examples = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", request_part, re.S)]
    assert examples, "the Request section of chat-events.md has no example"
    for example in examples:
        ChatRequest.model_validate(example)
