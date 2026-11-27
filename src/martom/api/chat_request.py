"""The body of POST /api/chat: the customer's message and, after chips, their answers.

The other half of the chat contract (the events it streams back are in chat_events.py).
The committed JSON Schema (docs/api/chat-request.schema.json) is generated from these
models, and web/src/api/chatClient.ts mirrors it. Regenerate both schemas with:

    uv run python -m martom.api.chat_events

Side lengths travel as a list of numbers plus an explicit `closed` flag (Tomislav,
27 Nov 2026), never as a string the agent parses: "10 + 5 + 10" is display text only.
The number of corners follows from them: len(sides) - 1 for an open fence, len(sides)
for a closed loop, the same rule as martom.bom.common.check_layout.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from martom.bom.common import MAX_SIDE_M, MIN_CLOSED_SIDES

SCHEMA_PATH = Path(__file__).parents[3] / "docs" / "api" / "chat-request.schema.json"

# One fence side in metres. A longer side gets no BOM (the cap rule in bom/common.py),
# so the request refuses it; the frontend checks the same limit before sending.
SideLength = Annotated[float, Field(gt=0, le=MAX_SIDE_M)]


class _Body(BaseModel):
    # extra="forbid": an unknown field (e.g. a stray "corners") is an error, as for the
    # events. frozen: read-only after parsing.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClarifyAnswers(_Body):
    """The customer's answers to a chips event."""

    choices: dict[str, str] = Field(
        default_factory=dict,
        description="Question id → picked option value, for the single questions.",
    )
    sides: list[SideLength] = Field(
        default_factory=list,
        description="Length of each fence side in metres, in order, each 0 < x ≤ 300. "
        "Empty when the chips had no layout question or the customer doesn't know "
        "the corners; then the length comes from the message.",
    )
    # StrictBool: only true/false, not "yes" or 1.
    closed: StrictBool = Field(
        default=False, description="The fence goes all the way round (a closed loop)."
    )

    @model_validator(mode="after")
    def _closed_needs_a_loop(self) -> Self:
        if self.closed and len(self.sides) < MIN_CLOSED_SIDES:
            raise ValueError(
                f"a closed fence needs at least {MIN_CLOSED_SIDES} sides, got {len(self.sides)}"
            )
        return self

    @property
    def corners(self) -> int | None:
        """len(sides) - 1 when open, len(sides) when closed; None without sides."""
        if not self.sides:
            return None
        return len(self.sides) if self.closed else len(self.sides) - 1


class ChatRequest(_Body):
    """One customer message, as the browser sends it."""

    message: str = Field(min_length=1, description="As typed, or the summary of the chip answers.")
    thread_id: str | None = Field(
        default=None, description="From the previous reply's done event; null at first."
    )
    answers: ClarifyAnswers | None = Field(
        default=None, description="Set when the message answers a chips event."
    )


def chat_request_schema() -> dict[str, Any]:
    """The JSON Schema of the request body, as the server accepts it."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **ChatRequest.model_json_schema(mode="validation"),
        "description": "The POST /api/chat request body. Generated from "
        "src/martom/api/chat_request.py; do not edit by hand.",
    }


def write_schema(path: Path = SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(chat_request_schema(), indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
