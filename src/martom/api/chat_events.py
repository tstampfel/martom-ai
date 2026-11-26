"""The events POST /api/chat streams to the browser (Server-Sent Events).

This module is the single source of the chat contract. Each SSE message carries one event
as JSON, and the `type` field says which model it is (a "discriminated union", like a
TypeScript union narrowed on `type`). The committed JSON Schema
(docs/api/chat-events.schema.json) is generated from these models, and the frontend types
in web/src/api/events.ts mirror that schema. Regenerate the schema after any change:

    uv run python -m martom.api.chat_events

Prices are strings with exactly two decimals ("53.00"), never floats, so no binary
rounding error reaches the customer. They come only from tools (database, BOM
calculator), never from the model. The endpoint itself lands with #21 [M4-4].
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
    badge: str | None = Field(default=None, description="e.g. 'po narudžbi, 10–15 dana'.")
    note: str | None = Field(default=None, description="Shown under the question when picked.")


class ChipQuestion(_Event):
    id: str = Field(description="e.g. 'corners', 'posts', 'color'.")
    text: str
    # single: pick one option. side_lengths: one number input (metres) per fence side,
    # as on docs/mockups/*/2-clarify.html; it has no options.
    kind: Literal["single", "side_lengths"]
    options: list[ChipOption]

    @model_validator(mode="after")
    def _options_match_kind(self) -> Self:
        if self.kind == "single" and not self.options:
            raise ValueError("a single-choice question needs options")
        if self.kind == "side_lengths" and self.options:
            raise ValueError("a side_lengths question has no options")
        return self


class ChipsEvent(_Event):
    """The clarify questions the advisor still needs answered."""

    type: Literal["chips"] = "chips"
    intro: str | None = Field(default=None, description="e.g. 'Još tri stvari:'.")
    questions: list[ChipQuestion] = Field(min_length=1)


# --- product cards ----------------------------------------------------------------


class Variant(_Event):
    label: str = Field(description="As written in the listing, e.g. '153 × 250'.")
    price: Price | None = Field(description="null: 'cijena na upit'.")


class ProductCard(_Event):
    listing_id: int = Field(ge=1)
    url: str = Field(
        pattern=LISTING_URL_PATTERN,
        description="https://martom.olx.ba/artikal/{slug}; the slug ends in listing_id.",
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
    def _slug_ends_in_listing_id(self) -> Self:
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

    name: str
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
    TokenEvent | StepEvent | ChipsEvent | ProductCardsEvent | BomEvent | ErrorEvent | DoneEvent,
    Field(discriminator="type"),
]

EVENT_TYPES: tuple[str, ...] = (
    "token",
    "step",
    "chips",
    "product_cards",
    "bom",
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
    # `python -m martom.api.chat_events [path]` regenerates the committed schema.
    write_schema(Path(sys.argv[1]) if len(sys.argv) > 1 else SCHEMA_PATH)
