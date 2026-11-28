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

from martom.agent.grounding import numbers_in
from martom.agent.understand import total_length_text
from martom.api import chat_copy
from martom.api.chat_events import (
    BomClearedEvent,
    BomEvent,
    CartEvent,
    CartLine,
    ChatEvent,
    ChipsEvent,
    ContactMartomEvent,
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
    """The reply after the turn, without `done`.

    [bom_cleared], [clarify step], tokens, then chips | (cards, bom, cart, contact).
    """
    events: list[ChatEvent] = []
    bom = state.get("bom")
    bom_changed = state.get("bom_changed")
    if bom_changed and not bom:
        # The fence changed (or can't be calculated) and the old list was dropped: tell
        # the browser first, so no stale list or total stays on screen (#147). It goes
        # on a clarify turn too, which is when a changed fence usually lands.
        events.append(BomClearedEvent())
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
    # Only a list this turn made: an unchanged one is already on screen.
    if bom and bom_changed:
        events.append(BomEvent.model_validate(_bom_event_fields(bom)))
    cart = cart_lines(state)
    if cart:
        events.append(CartEvent(lines=cart))
    if state.get("contact_martom"):
        events.append(ContactMartomEvent())
    return events


def cart_lines(state: Mapping[str, Any]) -> list[CartLine]:
    """This turn's cart additions (the add_to_cart results the graph collected).

    Title, link and price are the tool's; the line total is worked out in Python
    (`CartLine.from_tool`). The quantity is the model's own add_to_cart argument, so a
    line goes out only when it is grounded (`grounded_cart_quantities`, review R1-1):
    nothing on a fallback turn, whose answer grounding rejected. A line that breaks the
    contract or has an ungrounded quantity is dropped and logged by count only.
    """
    cart = state.get("cart") or []
    if not cart or state.get("fallback") or state.get("generate_failed"):
        return []
    allowed = grounded_cart_quantities(state)
    lines: list[CartLine] = []
    ungrounded = invalid = 0
    for cart_line in cart:
        if cart_line.get("quantity") not in allowed:
            ungrounded += 1
            continue
        try:
            lines.append(CartLine.from_tool(cart_line))
        except (ValueError, TypeError, ArithmeticError):
            invalid += 1
    if ungrounded or invalid:
        logger.info("cart lines dropped", extra={"ungrounded": ungrounded, "invalid": invalid})
    return lines


def grounded_cart_quantities(state: Mapping[str, Any]) -> set[int]:
    """The quantities a cart line may carry (Tomislav's rule for #147, review R1-1).

    A whole number the customer wrote in any of their messages, or the quantity of a line
    of the current materials list. 1 only when the customer's message this turn has no
    number at all ("Dodaj panel u korpu"), the natural default.
    """
    messages = state.get("messages", [])
    allowed: set[int] = set()
    for message in messages:
        if isinstance(message, HumanMessage):
            allowed.update(_whole_numbers(message.text))
    bom = state.get("bom") or {}
    allowed.update(
        line["quantity"] for line in bom.get("lines", []) if isinstance(line.get("quantity"), int)
    )
    last = _last_customer_message(messages)
    if last is None or not numbers_in(last.text):
        allowed.add(1)
    return allowed


def _whole_numbers(text: str) -> set[int]:
    return {int(number) for number in numbers_in(text) if number == number.to_integral_value()}


def _last_customer_message(messages: list[BaseMessage]) -> HumanMessage | None:
    return next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)


def search_step_text(count: int) -> str:
    """'Pronađen 1 proizvod', 'Pronađena 2 proizvoda', 'Pronađeno 5 proizvoda'.

    Bosnian agrees with the last digit (1; 2-4; the rest), except 11-14, which always
    take the "many" form: 21 is "proizvod", 12 is "proizvoda" with "Pronađeno".
    No products keeps the fixed text; the answer itself says nothing was found.
    """
    if count <= 0:
        return chat_copy.STEP_SEARCH
    last, last_two = count % 10, count % 100
    if last == 1 and last_two != 11:
        template = chat_copy.FOUND_ONE
    elif 2 <= last <= 4 and not 12 <= last_two <= 14:
        template = chat_copy.FOUND_FEW
    else:
        template = chat_copy.FOUND_MANY
    return template.format(count=count)


def parse_step_text(project: Mapping[str, Any] | None) -> str:
    """What the advisor understood about the fence, from the FenceSpec Python built.

    Only the fence type (an enum), the lengths and the height (numbers Python parsed),
    never text the model copied (such as a colour); the fixed text when none is known.
    """
    project = project or {}
    parts = []
    kind = {"panel": chat_copy.KIND_PANEL, "roll": chat_copy.KIND_ROLL}.get(
        project.get("kind") or ""
    )
    if kind:
        parts.append(kind)
    sides = project.get("sides_m") or []
    length = project.get("total_length_m")
    if sides:
        parts.append(total_length_text(sides))
    elif length:
        parts.append(total_length_text([length]))
    if project.get("height_cm"):
        parts.append(f"{project['height_cm']} cm")
    if not parts:
        return chat_copy.STEP_PARSE
    return chat_copy.PARSED.format(parts=chat_copy.PARSED_SEPARATOR.join(parts))


class StepTracker:
    """The "Savjetnik radi" steps from the graph's node updates, each sent once."""

    def __init__(self) -> None:
        self._sent: set[tuple[str, str]] = set()
        self._relevant = 0  # the latest grade's relevant hits (a rewrite grades again)

    def start(self) -> StepEvent:
        event = self._step("parse", chat_copy.STEP_PARSE, "working")
        assert event is not None, "start() is called once per turn"
        return event

    def update(self, node: str, changes: Mapping[str, Any] | None) -> list[StepEvent]:
        changes = changes or {}
        events: list[StepEvent | None] = []
        if node == "guardrail":
            events.append(self._step("parse", parse_step_text(changes.get("project")), "done"))
            if changes.get("route") == "retrieve":
                events.append(self._step("search", chat_copy.STEP_SEARCH, "working"))
        elif node == "grade":
            # The grade's own count (review R2-1): a weak search found nothing that fits,
            # however many hits came back, and keeps the fixed text.
            relevant = changes.get("relevant_hits") or 0
            self._relevant = relevant if changes.get("grade") == "ok" else 0
        elif node in {"generate", "grounding"} and ("search", "working") in self._sent:
            events.append(self._step("search", search_step_text(self._relevant), "done"))
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
        # No listing price: show the first priced variant, with its unit.
        priced = next((v for v in variants if v.get("price") is not None), None)
        if priced is not None:
            price, unit = priced["price"], priced.get("unit")
    else:
        # The listing price's unit is the one of the variant with that same price, if
        # any; otherwise no unit rather than a guess.
        unit = next((v.get("unit") for v in variants if v.get("price") == price), None)
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
