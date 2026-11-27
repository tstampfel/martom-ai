"""The grounding check: every price, listing id and quantity in an answer came from a tool.

CLAUDE.md: "Prices, product IDs and quantities in any answer come only from tools
(database, BOM calculator), never from the model." This is plain Python, no model:

- `facts_from_output` walks a tool's JSON output and collects what the model may repeat:
  prices (`price`, `unit_price`, `line_total`, `total`), listing ids (`listing_id`),
  quantities (`quantity`), and the names of lines that have no price (`price_on_request`).
- `check_grounding` finds every price ("53,00 KM"), listing id (7–9 digits) and quantity
  ("10 panela", "44 kom") in the answer and reports each one that isn't in the facts.
  A sentence that names a price together with something that never has one (a gate,
  installation, "po narudžbi", "na upit", concrete posts, or an unpriced BOM line) fails
  even when the amount itself is known: the model must not attach a price to those.

The check is deliberately strict: a false alarm costs a retry (and at worst the plain
search results), a missed one would show the customer an invented price.
"""

# ruff: noqa: RUF002, RUF003

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

PRICE_KEYS = frozenset({"price", "unit_price", "line_total", "total"})

# "1.234,50 KM" (thousands dots), "53,00 KM", "24.90 KM", "53 KM", also "BAM".
_PRICE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:KM|BAM)\b",
    re.IGNORECASE,
)
# OLX listing ids have 7 to 9 digits. Martom's phone (036 651 427) is in 3-digit groups.
_LISTING_ID = re.compile(r"(?<![\d.,])\d{7,9}(?!\d|[.,]\d)")
# A count followed by a piece word. Sizes ("153 cm", "25 m") and "153 × 250" never match.
_QUANTITY = re.compile(
    r"(?<![\d.,])(\d+)\s+(?:kom\b|komad\w*|panel\w*|stup\w*|kopč\w*|kopc\w*|anker\w*|"
    r"rol\w*|zatezač\w*|zatezac\w*|španer\w*|spaner\w*|kolut\w*|nosač\w*|nosac\w*)",
    re.IGNORECASE,
)
# Things that never carry a price in an answer (fence-rules.md).
NEVER_PRICED = (
    "na upit",
    "po upitu",
    "po narudžbi",
    "po narudzbi",
    "kapij",
    "montaž",
    "montaz",
    "betoniranj",
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class Facts(BaseModel):
    """What the tools returned this turn; kept in the graph state as plain JSON."""

    prices: list[str] = Field(default_factory=list)  # Decimal as text, e.g. "53.00"
    listing_ids: list[int] = Field(default_factory=list)
    quantities: list[int] = Field(default_factory=list)
    unpriced_names: list[str] = Field(default_factory=list)

    def merge(self, other: "Facts") -> "Facts":
        return Facts(
            prices=_unique(self.prices + other.prices),
            listing_ids=_unique(self.listing_ids + other.listing_ids),
            quantities=_unique(self.quantities + other.quantities),
            unpriced_names=_unique(self.unpriced_names + other.unpriced_names),
        )

    def price_set(self) -> set[Decimal]:
        # Decimal("53.00") == Decimal("53"), so "53 KM" matches a "53.00" fact.
        return {Decimal(price) for price in self.prices}


@dataclass(frozen=True)
class GroundingResult:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def facts_from_output(output: Any) -> Facts:
    """Every price, listing id, quantity and unpriced line name in a tool's JSON output."""
    prices: list[str] = []
    listing_ids: list[int] = []
    quantities: list[int] = []
    unpriced: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in PRICE_KEYS and item is not None:
                    price = _decimal(item)
                    if price is not None:
                        prices.append(str(price))
                elif key == "listing_id" and _is_int(item):
                    listing_ids.append(item)
                elif key == "quantity" and _is_int(item):
                    quantities.append(item)
                walk(item)
            if value.get("price_on_request") is True and isinstance(value.get("name"), str):
                unpriced.append(value["name"])
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(output)
    return Facts(
        prices=_unique(prices),
        listing_ids=_unique(listing_ids),
        quantities=_unique(quantities),
        unpriced_names=_unique(unpriced),
    )


def parse_km(text: str) -> Decimal | None:
    """'1.234,50' -> 1234.50, '53,00' -> 53.00, '24.90' -> 24.90; None if not a number."""
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")  # "1.234" with no decimals: thousands dots
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def check_grounding(text: str, facts: Facts) -> GroundingResult:
    """Each problem is one short English line, for the retry prompt and the logs."""
    problems: list[str] = []
    known_prices = facts.price_set()
    for match in _PRICE.finditer(text):
        amount = parse_km(match.group(1))
        if amount is None or amount not in known_prices:
            problems.append(f"price '{match.group(0)}' is not in any tool result")
    known_ids = set(facts.listing_ids)
    for match in _LISTING_ID.finditer(text):
        if int(match.group(0)) not in known_ids:
            problems.append(f"listing id {match.group(0)} is not in any tool result")
    known_quantities = set(facts.quantities)
    for match in _QUANTITY.finditer(text):
        if int(match.group(1)) not in known_quantities:
            problems.append(f"quantity '{match.group(0)}' is not in any tool result")
    problems.extend(_priced_never_priced(text, facts.unpriced_names))
    return GroundingResult(problems)


def _priced_never_priced(text: str, unpriced_names: Iterable[str]) -> list[str]:
    words = [*NEVER_PRICED, *(name.lower() for name in unpriced_names)]
    problems = []
    for sentence in _SENTENCE_END.split(text):
        lowered = sentence.lower()
        if _PRICE.search(sentence) and any(word in lowered for word in words):
            problems.append(
                f"sentence '{sentence.strip()}' gives a price for something that is "
                "never priced (gate, installation, made to order or on request)"
            )
    return problems


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unique[T](items: list[T]) -> list[T]:
    # dict keeps insertion order (like a JS Map), so this dedupes without reordering.
    return list(dict.fromkeys(items))
