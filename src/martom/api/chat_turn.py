"""Turn the advisor graph's output into chat events (#21): plain functions, no I/O.

`StepTracker` turns the graph's node updates into "Savjetnik radi" steps while the turn
runs; `final_events` turns the finished turn's state into the reply. The router
(api/chat.py) sends both and adds `done`.

The answer text is sent only after the grounding check passed (docs/PLAN.md: every
price and number comes from a tool). So `token` events are never the model's live
tokens: the graph's final, checked `answer` is cut into chunks of a few words, which
the browser appends exactly as it would live tokens.
"""

import json
import logging
import re
from collections.abc import Mapping
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from martom.api import chat_copy
from martom.api.chat_events import (
    BomEvent,
    ChatEvent,
    ChipsEvent,
    ProductCard,
    ProductCardsEvent,
    StepEvent,
    TokenEvent,
)

logger = logging.getLogger(__name__)

WORDS_PER_TOKEN = 6
# A word and the spaces after it; a leading run of spaces is its own piece.
_PIECE = re.compile(r"\S+\s*|\s+")


def chunk_answer(text: str, words: int = WORDS_PER_TOKEN) -> list[str]:
    """`text` in chunks of `words` words; joined back together they are `text` exactly."""
    pieces = _PIECE.findall(text)
    return ["".join(pieces[i : i + words]) for i in range(0, len(pieces), words)]


def product_cards(state: Mapping[str, Any]) -> list[ProductCard]:
    """Cards for the products the advisor looked up (get_product) in this turn.

    Only tool results: titles, links and prices come from the database or
    manual_products.toml, never from the model. A product looked up twice is one card.
    `why` stays empty until a grounded "why it fits" exists (Tomislav decides, #21).
    """
    cards: list[ProductCard] = []
    seen: set[tuple[str, object]] = set()
    for message in _this_turn(state.get("messages", [])):
        if not isinstance(message, ToolMessage) or message.name != "get_product":
            continue
        if message.status == "error":
            continue
        try:
            output = json.loads(message.text)
            card = _card(output)
        except (ValueError, TypeError, KeyError) as exc:
            # pydantic's ValidationError is a ValueError; e.g. a listing without a link.
            logger.info("product card skipped", extra={"error": type(exc).__name__})
            continue
        key = (card.source, card.listing_id or card.manual_key)
        if key not in seen:
            seen.add(key)
            cards.append(card)
    return cards


def final_events(state: Mapping[str, Any]) -> list[ChatEvent]:
    """The reply after the turn: [clarify step], tokens, chips | cards, bom (no `done`)."""
    events: list[ChatEvent] = []
    chips = state.get("chips")
    if chips:
        events.append(StepEvent(id="clarify", text=chat_copy.STEP_CLARIFY, status="needs_input"))
    events += [TokenEvent(text=chunk) for chunk in chunk_answer(state.get("answer") or "")]
    if chips:
        events.append(ChipsEvent.model_validate(chips))
        return events
    cards = product_cards(state)
    if cards:
        events.append(ProductCardsEvent(cards=cards))
    bom = state.get("bom")
    # Only a list this turn made: an unchanged one is already on screen. A cleared list
    # (the fence changed) has no event yet; the next calculation replaces it.
    if bom and state.get("bom_changed"):
        events.append(BomEvent.model_validate(_bom_event_fields(bom)))
    return events


class StepTracker:
    """The "Savjetnik radi" steps from the graph's node updates, each sent once."""

    def __init__(self) -> None:
        self._sent: set[tuple[str, str]] = set()

    def start(self) -> StepEvent:
        event = self._step("parse", chat_copy.STEP_PARSE, "working")
        assert event is not None, "start() is called once per turn"
        return event

    def update(self, node: str, changes: Mapping[str, Any] | None) -> list[StepEvent]:
        changes = changes or {}
        events: list[StepEvent | None] = []
        if node == "guardrail":
            events.append(self._step("parse", chat_copy.STEP_PARSE, "done"))
            if changes.get("route") == "retrieve":
                events.append(self._step("search", chat_copy.STEP_SEARCH, "working"))
        elif node in {"generate", "grounding"} and ("search", "working") in self._sent:
            events.append(self._step("search", chat_copy.STEP_SEARCH, "done"))
        if node == "tools" and changes.get("bom") and changes.get("bom_changed"):
            events.append(self._step("bom", chat_copy.STEP_BOM, "done"))
        return [event for event in events if event is not None]

    def _step(
        self, step_id: str, text: str, status: Literal["working", "done", "needs_input"]
    ) -> StepEvent | None:
        if (step_id, status) in self._sent:
            return None
        self._sent.add((step_id, status))
        return StepEvent(id=step_id, text=text, status=status)


def _this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The messages after the customer's last one."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return list(messages)


def _card(output: dict[str, Any]) -> ProductCard:
    variants = output.get("variants") or []
    price, unit = output.get("price"), None
    if price is None:
        priced = next((v for v in variants if v.get("price") is not None), None)
        if priced is not None:
            price, unit = priced["price"], priced.get("unit")
    return ProductCard(
        source=output["source"],
        listing_id=output.get("listing_id"),
        manual_key=output.get("manual_key"),
        url=output.get("url"),
        title=output["title"],
        why="",
        price=price,
        price_unit=unit,
        variants=[{"label": v["label"], "price": v.get("price")} for v in variants],  # type: ignore[misc]
    )


def _bom_event_fields(bom: Mapping[str, Any]) -> dict[str, Any]:
    # calculate_fence_bom's output is a bom event plus the suggested wire add-on, which
    # the contract doesn't carry (yet): drop the extra keys before validating.
    return {key: value for key, value in bom.items() if key in BomEvent.model_fields}
