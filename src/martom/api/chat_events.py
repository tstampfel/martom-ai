"""The events POST /api/chat streams to the browser (Server-Sent Events).

This module is the single source of the chat contract. Each SSE message carries one event
as JSON, and the `type` field says which model it is (a "discriminated union", like a
TypeScript union narrowed on `type`). The committed JSON Schema
(docs/api/chat-events.schema.json) is generated from these models, and the frontend types
in web/src/api/events.ts mirror that schema. Regenerate the schema after any change:

    uv run python -m martom.api.chat_events

Prices are strings with exactly two decimals ("53.00"), never floats, so no binary
rounding error reaches the customer. They come only from tools (database, BOM
calculator), never from the model. The endpoint is api/chat.py (#21 [M4-4]).
"""

# Field descriptions quote Bosnian labels with the real "×" and "–" signs.
# ruff: noqa: RUF001, RUF002, RUF003

import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from martom.bom.common import BomLine, Item

SCHEMA_PATH = Path(__file__).parents[3] / "docs" / "api" / "chat-events.schema.json"

CENT = Decimal("0.01")
# [0-9], not \d: Python's \d also matches non-ASCII digits, which the browser rejects.
PRICE_PATTERN = r"^[0-9]+\.[0-9]{2}$"
# Every listing link opens on the shop subdomain; olx.ba/artikal/{id} does not open.
LISTING_URL_PATTERN = r"^https://martom\.olx\.ba/artikal/[a-z0-9-]+$"


def format_price(amount: Decimal) -> str:
    """A Decimal amount in KM as the wire format: "53.00" (rounded half up to cents)."""
    return str(round_to_cents(amount))


def round_to_cents(amount: Decimal) -> Decimal:
    """Round half up to whole cents; ValueError for a price that can't be one."""
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"a price must be a non-negative amount, got {amount}")
    try:
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # More digits than Decimal's precision (e.g. 1e30). Not a ValueError by itself,
        # so pydantic would let it escape instead of reporting a ValidationError.
        raise ValueError(f"price {amount} is too large") from None


def _to_price(value: object) -> object:
    # Runs before the string checks (BeforeValidator): a Decimal becomes "53.00"; a
    # float or int is refused outright, so a model-made or float price can't slip in.
    if isinstance(value, Decimal):
        return format_price(value)
    if not isinstance(value, str):
        raise ValueError(f"a price must be a Decimal or a string like '53.00', got {value!r}")
    return value


def _known_item(value: str) -> str:
    if value not in {item.value for item in Item}:
        raise ValueError(f"{value!r} is not a BOM item")
    return value


# `Annotated[str, ...]` is a str with extra rules attached, much like a branded type in TS.
Price = Annotated[str, BeforeValidator(_to_price), StringConstraints(pattern=PRICE_PATTERN)]
# A BOM item key (martom.bom.common.Item). A plain string in the schema on purpose: the
# frontend only uses it as a key, so adding or removing a BOM item changes no contract.
BomItemKey = Annotated[str, AfterValidator(_known_item)]


class _Event(BaseModel):
    # extra="forbid": an unknown field is an error (and `additionalProperties: false` in
    # the schema), so a contract change can't go unnoticed. frozen: read-only after
    # creation. defaults_required: the serialised JSON always has every field (null when
    # empty), so the TS types need no optional `?` fields.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )


# --- token, step ------------------------------------------------------------------


class TokenEvent(_Event):
    """A piece of the advisor's answer text; append it to the current message."""

    type: Literal["token"] = "token"
    text: str


class StepEvent(_Event):
    """A "Savjetnik radi" step. Sent again with the same id when its status changes."""

    type: Literal["step"] = "step"
    id: str = Field(description="Stable per step within one reply, e.g. 'parse', 'search'.")
    text: str = Field(description="Bosnian, e.g. 'Pregledao 39 ograda'.")
    status: Literal["working", "done", "needs_input"]


# --- chips (clarify) --------------------------------------------------------------


class ChipOption(_Event):
    value: str = Field(description="Sent back in the next message; not shown.")
    label: str
    short_label: str | None = Field(
        default=None,
        description="Shorter text for the 'Vaš projekt' panel, e.g. 'Antracit'; else label.",
    )
    badge: str | None = Field(default=None, description="e.g. 'po narudžbi, 10–15 dana'.")
    note: str | None = Field(default=None, description="Shown under the question when picked.")


class ChipQuestion(_Event):
    id: str = Field(description="e.g. 'posts', 'color'; the key of its answer in choices.")
    text: str = Field(description="The full question, e.g. 'Gdje idu stupovi?'.")
    label: str | None = Field(
        default=None, description="Short row label for the 'Vaš projekt' panel, e.g. 'Stupovi'."
    )
    # single: pick one option; the answer goes in ChatRequest.answers.choices[id].
    # layout: the fence layout (docs/mockups/*/2-clarify.html): corner chips, the "Ide
    # okolo" toggle and one length per side, all drawn by the frontend. It has no
    # options; the answer goes in ChatRequest.answers.sides and .closed.
    kind: Literal["single", "layout"]
    options: list[ChipOption]
    default: str | None = Field(
        default=None,
        description="Option value picked before the customer taps, e.g. 'plate' "
        "(base-plate posts, fence-rules.md). Only for single questions.",
    )

    @model_validator(mode="after")
    def _options_match_kind(self) -> Self:
        if self.kind == "single" and not self.options:
            raise ValueError("a single-choice question needs options")
        if self.kind == "layout" and self.options:
            raise ValueError("a layout question has no options")
        if self.default is not None:
            if self.kind == "layout":
                raise ValueError("a layout question has no default")
            if self.default not in {option.value for option in self.options}:
                raise ValueError(f"default {self.default!r} is not one of the option values")
        return self


class ProjectFact(_Event):
    """Something the advisor already knows from the message, e.g. Visina: oko 150 cm."""

    label: str = Field(min_length=1, description="e.g. 'Visina'.")
    value: str = Field(min_length=1, description="e.g. 'oko 150 cm'.")


class ProjectSummary(_Event):
    """What the 'Vaš projekt' panel shows before the customer answers anything."""

    title: str = Field(min_length=1, description="e.g. 'Ograda za dvorište'.")
    length_m: float | None = Field(
        default=None,
        gt=0,
        description="Total length understood from the message, in metres (e.g. 25). "
        "A straight fence (0 corners) sends it back as its one side.",
    )
    facts: list[ProjectFact] = Field(default_factory=list)
    panel_order: list[str] = Field(
        default_factory=list,
        description="Single question ids in the order the panel lists them, e.g. "
        "['color', 'posts'] (the panel's order can differ from the form's). Questions "
        "not listed follow in question order.",
    )


class ChipsEvent(_Event):
    """The clarify questions the advisor still needs answered."""

    type: Literal["chips"] = "chips"
    intro: str | None = Field(default=None, description="e.g. 'Još tri stvari:'.")
    project: ProjectSummary | None = Field(
        default=None, description="Title, length and facts for the 'Vaš projekt' panel."
    )
    questions: list[ChipQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _questions_fit_together(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"question ids must be unique, got {ids}")
        if sum(1 for question in self.questions if question.kind == "layout") > 1:
            raise ValueError("a chips event has at most one layout question")
        if self.project is not None:
            order = self.project.panel_order
            singles = {question.id for question in self.questions if question.kind == "single"}
            if len(set(order)) != len(order) or not set(order) <= singles:
                raise ValueError(
                    f"panel_order {order} must name each single question id at most once"
                )
        return self


# --- product cards ----------------------------------------------------------------


class Variant(_Event):
    label: str = Field(description="As written in the listing, e.g. '153 × 250'.")
    price: Price | None = Field(description="null: 'cijena na upit'.")


class ProductCard(_Event):
    """One product: an OLX listing, or a manual product (#132) without one.

    A manual product (`source` "manual", from manual_products.toml) has no listing id and
    no link, only its `manual_key`; never build an olx.ba link for it.
    """

    source: Literal["olx", "manual"] = Field(
        default="olx", description="'manual': sold without an OLX listing (no link)."
    )
    listing_id: int | None = Field(default=None, ge=1, description="null for a manual product.")
    manual_key: str | None = Field(
        default=None, description="The manual_products.toml key; null for an OLX product."
    )
    url: str | None = Field(
        default=None,
        pattern=LISTING_URL_PATTERN,
        description="https://martom.olx.ba/artikal/{slug}; the slug ends in listing_id. "
        "null for a manual product.",
    )
    title: str
    subtitle: str | None = None
    why: str = Field(description="Why it fits this customer, in Bosnian.")
    badge: str | None = Field(default=None, description="e.g. 'Preporuka', 'Jeftinije'.")
    photo_url: str | None = None
    price: Price | None = Field(description="Price of the shown variant; null: 'na upit'.")
    price_unit: str | None = Field(default=None, description="e.g. 'panel', 'm', 'kom'.")
    variants: list[Variant] = Field(default_factory=list)

    @model_validator(mode="after")
    def _matches_source(self) -> Self:
        if self.source == "manual":
            if not self.manual_key:
                raise ValueError("a manual product card needs its manual_key")
            if self.listing_id is not None:
                raise ValueError("a manual product card has no listing_id")
            if self.url is not None:
                raise ValueError("a manual product card has no url (no OLX listing)")
            return self
        if self.manual_key is not None:
            raise ValueError("an OLX product card has no manual_key")
        if self.listing_id is None:
            raise ValueError("an OLX product card needs its listing_id")
        if self.url is None:
            raise ValueError("an OLX product card needs its url")
        if not self.url.endswith(f"-{self.listing_id}"):
            raise ValueError(f"url {self.url!r} must end in -{{listing_id}} ({self.listing_id})")
        return self


class ProductCardsEvent(_Event):
    type: Literal["product_cards"] = "product_cards"
    cards: list[ProductCard] = Field(min_length=1)


# --- bom --------------------------------------------------------------------------


class BomLineOut(_Event):
    """One line of the materials list, as the BOM calculator made it, with its price."""

    item: BomItemKey = Field(description="BOM item key, e.g. 'panel'; use it as a key only.")
    name: str
    size: str | None = None
    quantity: int = Field(ge=1)
    unit_price: Price | None
    line_total: Price | None
    price_on_request: bool = Field(description="true: show 'cijena na upit', no price.")
    made_to_order: bool = Field(description="'po narudžbi': Toni confirms it.")
    label: str | None = Field(default=None, description="e.g. 'po narudžbi, 10–15 dana'.")

    @model_validator(mode="after")
    def _prices_add_up(self) -> Self:
        if self.price_on_request:
            if self.unit_price is not None or self.line_total is not None:
                raise ValueError("a price_on_request line has no unit_price or line_total")
            return self
        if self.unit_price is None or self.line_total is None:
            raise ValueError("a line without a price must be price_on_request")
        expected = format_price(Decimal(self.unit_price) * self.quantity)
        if self.line_total != expected:
            raise ValueError(f"line_total {self.line_total} must be {expected}")
        return self

    @classmethod
    def from_line(cls, line: BomLine, unit_price: Decimal | None) -> Self:
        """The wire line for a calculator line; unit_price comes from the database.

        Raises ValueError for a price that can't be one (negative, infinite, too large).
        pydantic's ValidationError is a ValueError too, so one `except ValueError`
        catches every bad input.
        """
        # An unconfirmed item never shows a price, even when one was found.
        if line.unit_price_on_request or unit_price is None:
            prices: dict[str, Any] = {"unit_price": None, "line_total": None}
        else:
            # Round the unit price first (as the BOM shows it), then multiply, so the
            # total always equals the shown price × quantity.
            unit = round_to_cents(unit_price)
            prices = {"unit_price": unit, "line_total": unit * line.quantity}
        return cls(
            item=line.item.value,
            name=line.name,
            size=line.size,
            quantity=line.quantity,
            price_on_request=prices["unit_price"] is None,
            made_to_order=line.made_to_order,
            label=line.label,
            **prices,
        )


class ExtraLine(_Event):
    """A no-price order line that is not part of the BOM calculation.

    E.g. "Stupovi za betoniranje – po upitu" when the customer sets the posts in
    concrete (Tomislav, 26 Nov 2026; built in #20 and #27). It has no quantity and no
    price field at all, and it never counts in `total` or `unpriced_count`.
    """

    name: str = Field(min_length=1)
    note: str | None = None


class BomEvent(_Event):
    """The materials list (popis materijala) with an approximate total."""

    type: Literal["bom"] = "bom"
    title: str
    summary: str = Field(description="e.g. '25 m · panel 153 cm · antracit'.")
    lines: list[BomLineOut] = Field(min_length=1)
    total: Price | None = Field(
        description="Sum of the priced lines; null when every line is 'na upit' "
        "(never a fake '0.00')."
    )
    unpriced_count: int = Field(ge=0, description="How many lines are 'na upit'.")
    extra_lines: list[ExtraLine] = Field(
        default_factory=list,
        description="No-price order lines outside the BOM, e.g. concrete posts 'po upitu'.",
    )
    notes: list[str] = Field(default_factory=list, description="Extra notes in Bosnian.")

    @model_validator(mode="after")
    def _total_matches_lines(self) -> Self:
        on_request = sum(1 for line in self.lines if line.price_on_request)
        if self.unpriced_count != on_request:
            raise ValueError(
                f"unpriced_count {self.unpriced_count} must be the number of "
                f"price_on_request lines, {on_request}"
            )
        priced = [Decimal(line.line_total) for line in self.lines if line.line_total]
        expected = format_price(sum(priced, Decimal(0))) if priced else None
        if self.total != expected:
            raise ValueError(
                f"total {self.total} must be {expected}: the sum of the priced lines, "
                "or null when every line is price_on_request"
            )
        return self


class BomClearedEvent(_Event):
    """The materials list shown so far no longer fits: hide it and its total.

    Sent when the customer changed the fence (type, height, sides, posts, colour, ...)
    and the advisor dropped the old list (`bom_changed` with no new list), also on a
    turn that asks clarify questions. The next `bom` event brings the new list
    (Tomislav, 28 Nov 2026, #147: no stale list or total may stay on screen).
    """

    type: Literal["bom_cleared"] = "bom_cleared"


# --- cart, contact_martom ---------------------------------------------------------


class CartLine(_Event):
    """One product the advisor added to the cart (korpa) this turn (add_to_cart tool).

    Title, link, variant and price come from the database or manual_products.toml; the
    quantity is the one add_to_cart accepted. A manual product (#132) has no listing id
    and no link, like a manual ProductCard.
    """

    source: Literal["olx", "manual"]
    listing_id: int | None = Field(ge=1, description="null for a manual product.")
    manual_key: str | None = Field(description="The manual_products.toml key, else null.")
    url: str | None = Field(
        pattern=LISTING_URL_PATTERN,
        description="https://martom.olx.ba/artikal/{slug}; null for a manual product.",
    )
    title: str = Field(min_length=1)
    variant_label: str | None = Field(
        description="As written in the listing, e.g. '153 × 250'; null without variants."
    )
    quantity: int = Field(ge=1)
    unit_price: Price | None = Field(description="null: 'cijena na upit'.")
    line_total: Price | None = Field(description="unit_price × quantity; null when on request.")

    @model_validator(mode="after")
    def _matches_source_and_adds_up(self) -> Self:
        if self.source == "manual":
            if not self.manual_key:
                raise ValueError("a manual cart line needs its manual_key")
            if self.listing_id is not None:
                raise ValueError("a manual cart line has no listing_id")
            if self.url is not None:
                raise ValueError("a manual cart line has no url (no OLX listing)")
        else:
            if self.manual_key is not None:
                raise ValueError("an OLX cart line has no manual_key")
            if self.listing_id is None or self.url is None:
                raise ValueError("an OLX cart line needs its listing_id and url")
            if not self.url.endswith(f"-{self.listing_id}"):
                raise ValueError(f"url {self.url!r} must end in -{{listing_id}}")
        expected = (
            None
            if self.unit_price is None
            else format_price(Decimal(self.unit_price) * self.quantity)
        )
        if self.line_total != expected:
            raise ValueError(f"line_total {self.line_total} must be {expected}")
        return self

    @classmethod
    def from_tool(cls, cart_line: dict[str, Any]) -> Self:
        """The wire line for one `cart_line` that agent/tools.py `_add_to_cart` returned.

        The total is worked out here in Python, so neither the model nor the browser
        multiplies a price. Raises ValueError (a pydantic ValidationError) for a line
        that breaks the contract.
        """
        unit_price = cart_line.get("unit_price")
        quantity = cart_line.get("quantity")
        line_total = None
        if unit_price is not None and isinstance(quantity, int):
            line_total = format_price(Decimal(unit_price) * quantity)
        return cls.model_validate({**cart_line, "line_total": line_total})


class CartEvent(_Event):
    """Add these lines to the cart (korpa) the browser keeps; only this turn's additions."""

    type: Literal["cart"] = "cart"
    lines: list[CartLine] = Field(min_length=1)


class ContactMartomEvent(_Event):
    """Offer the "contact Martom" button: the advisor can't make a list for this request.

    Sent for a chain-link fence (no rules yet, fence-rules.md) and a fence too large to
    calculate. The answer text says why; the browser offers the WhatsApp order button
    with the customer's request as the note (the order form is #27).
    """

    type: Literal["contact_martom"] = "contact_martom"


# --- error, done ------------------------------------------------------------------


class ErrorEvent(_Event):
    """The reply failed. The frontend shows its own Bosnian text for each kind."""

    type: Literal["error"] = "error"
    kind: Literal["rate_limited", "invalid_request", "internal"]
    retry_after_seconds: int | None = Field(
        default=None, ge=1, description="Only for rate_limited: when to try again."
    )

    @model_validator(mode="after")
    def _retry_only_when_rate_limited(self) -> Self:
        if self.retry_after_seconds is not None and self.kind != "rate_limited":
            raise ValueError("retry_after_seconds belongs only to a rate_limited error")
        return self


class DoneEvent(_Event):
    """The last event of every reply, also after an error."""

    type: Literal["done"] = "done"
    thread_id: str = Field(description="Send it with the next message to continue.")


ChatEvent = Annotated[
    TokenEvent
    | StepEvent
    | ChipsEvent
    | ProductCardsEvent
    | BomEvent
    | BomClearedEvent
    | CartEvent
    | ContactMartomEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="type"),
]

EVENT_TYPES: tuple[str, ...] = (
    "token",
    "step",
    "chips",
    "product_cards",
    "bom",
    "bom_cleared",
    "cart",
    "contact_martom",
    "error",
    "done",
)

_adapter: TypeAdapter[ChatEvent] = TypeAdapter(ChatEvent)


def parse_event(data: Any) -> ChatEvent:
    """Validate a dict (e.g. decoded JSON) as one chat event; raise ValidationError."""
    return _adapter.validate_python(data)


def chat_event_schema() -> dict[str, Any]:
    """The JSON Schema of one event, as the browser receives it (serialisation mode)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ChatEvent",
        "description": "One event of the POST /api/chat SSE stream. Generated from "
        "src/martom/api/chat_events.py; do not edit by hand.",
        **_adapter.json_schema(mode="serialization"),
    }


def write_schema(path: Path = SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(chat_event_schema(), indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    # `python -m martom.api.chat_events` regenerates both committed schemas: the events
    # and the request body (chat_request.py). With a path it writes only the events.
    if len(sys.argv) > 1:
        write_schema(Path(sys.argv[1]))
    else:
        from martom.api import chat_request

        write_schema()
        chat_request.write_schema()
