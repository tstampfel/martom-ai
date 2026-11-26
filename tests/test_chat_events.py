"""The POST /api/chat SSE event contract: models, JSON Schema and docs agree."""

# The labels under test use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from martom.api.chat_events import (
    EVENT_TYPES,
    BomEvent,
    BomLineOut,
    ErrorEvent,
    ProductCard,
    chat_event_schema,
    parse_event,
)
from martom.bom.common import BomLine, Item

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ROOT / "docs" / "api" / "chat-events.schema.json"
DOCS_PATH = ROOT / "docs" / "api" / "chat-events.md"


def panel_line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "item": "panel",
        "name": "Panel 153 × 250 cm, antracit",
        "size": "153 × 250 cm",
        "quantity": 10,
        "unit_price": "53.00",
        "line_total": "530.00",
        "price_on_request": False,
        "made_to_order": False,
        "label": None,
    }
    line.update(overrides)
    return line


def bom_event(lines: list[dict[str, Any]], total: str, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "bom",
        "title": "Popis materijala",
        "summary": "25 m · panel 153 cm · antracit",
        "lines": lines,
        "total": total,
        "notes": [],
    }
    event.update(overrides)
    return event


def card(**overrides: Any) -> dict[str, Any]:
    product: dict[str, Any] = {
        "listing_id": 63996799,
        "url": "https://martom.olx.ba/artikal/panelna-ograda-3d-63996799",
        "title": "Panelna ograda 3D (Nylofor)",
        "subtitle": "Žica Ø 4,20/4,40 mm · plastificirana",
        "why": "Čvrsta i uredna, ne treba je zatezati.",
        "badge": "Preporuka",
        "photo_url": None,
        "price": "53.00",
        "price_unit": "panel",
        "variants": [{"label": "153 × 250", "price": "53.00"}],
    }
    product.update(overrides)
    return product


# --- prices -----------------------------------------------------------------------


def test_price_from_decimal_is_a_two_decimal_string() -> None:
    line = BomLineOut.model_validate(
        panel_line(unit_price=Decimal("53"), line_total=Decimal("530.0"))
    )
    assert line.unit_price == "53.00"
    assert line.line_total == "530.00"


def test_price_rounds_half_up_to_cents() -> None:
    line = BomLineOut.model_validate(
        panel_line(quantity=1, unit_price=Decimal("1.005"), line_total=Decimal("1.005"))
    )
    assert line.unit_price == "1.01"


@pytest.mark.parametrize("bad", [53.0, "53", "53.0", "53,00", "-1.00", "1e2", True])
def test_price_rejects_floats_and_badly_written_strings(bad: object) -> None:
    with pytest.raises(ValidationError):
        BomLineOut.model_validate(panel_line(quantity=1, unit_price=bad, line_total=bad))


# --- the union --------------------------------------------------------------------


def test_every_event_type_parses_by_its_type_field() -> None:
    events = [
        {"type": "token", "text": "Odlično"},
        {"type": "step", "id": "parse", "text": "Razumio: 25 m", "status": "done"},
        {
            "type": "chips",
            "intro": "Još tri stvari:",
            "questions": [
                {
                    "id": "color",
                    "text": "Koja boja?",
                    "kind": "single",
                    "options": [{"value": "antracit", "label": "Antracit (RAL 7016)"}],
                }
            ],
        },
        {"type": "product_cards", "cards": [card()]},
        bom_event([panel_line()], "530.00"),
        {"type": "error", "kind": "internal", "retry_after_seconds": None},
        {"type": "done", "thread_id": "t-1"},
    ]
    parsed = [parse_event(event) for event in events]
    assert [event.type for event in parsed] == list(EVENT_TYPES)


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event({"type": "debug", "text": "x"})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event({"type": "token", "text": "x", "extra": 1})


def test_option_defaults_are_written_out() -> None:
    event = parse_event(
        {
            "type": "chips",
            "intro": None,
            "questions": [
                {
                    "id": "posts",
                    "text": "Gdje idu stupovi?",
                    "kind": "single",
                    "options": [{"value": "plate", "label": "Na beton, s pločicom"}],
                }
            ],
        }
    )
    option = event.model_dump(mode="json")["questions"][0]["options"][0]
    assert option == {
        "value": "plate",
        "label": "Na beton, s pločicom",
        "badge": None,
        "note": None,
    }


def test_choice_question_needs_options() -> None:
    with pytest.raises(ValidationError, match="options"):
        parse_event(
            {
                "type": "chips",
                "intro": None,
                "questions": [{"id": "c", "text": "Boja?", "kind": "single", "options": []}],
            }
        )


# --- product cards ----------------------------------------------------------------


def test_product_card_url_must_be_the_martom_shop_link() -> None:
    with pytest.raises(ValidationError, match="url"):
        ProductCard.model_validate(card(url="https://olx.ba/artikal/63996799"))


def test_product_card_slug_must_end_in_the_listing_id() -> None:
    with pytest.raises(ValidationError, match="listing_id"):
        ProductCard.model_validate(card(listing_id=12345))


def test_product_card_without_price_is_on_request() -> None:
    product = ProductCard.model_validate(card(price=None))
    assert product.price is None


# --- bom --------------------------------------------------------------------------


def test_bom_line_total_must_be_unit_price_times_quantity() -> None:
    with pytest.raises(ValidationError, match="line_total"):
        BomLineOut.model_validate(panel_line(line_total="500.00"))


def test_bom_line_on_request_has_no_prices() -> None:
    with pytest.raises(ValidationError, match="price_on_request"):
        BomLineOut.model_validate(panel_line(price_on_request=True))


def test_bom_line_without_price_must_be_on_request() -> None:
    with pytest.raises(ValidationError, match="price_on_request"):
        BomLineOut.model_validate(panel_line(unit_price=None, line_total=None))


def test_bom_quantity_is_at_least_one() -> None:
    with pytest.raises(ValidationError):
        BomLineOut.model_validate(panel_line(quantity=0, line_total="0.00"))


def test_bom_total_is_the_sum_of_priced_lines() -> None:
    on_request = panel_line(
        item="post_concrete",
        name="Stup za betoniranje 125 cm",
        size="125 cm",
        quantity=11,
        unit_price=None,
        line_total=None,
        price_on_request=True,
    )
    event = BomEvent.model_validate(bom_event([panel_line(), on_request], "530.00"))
    assert event.total == "530.00"
    with pytest.raises(ValidationError, match="total"):
        BomEvent.model_validate(bom_event([panel_line()], "531.00"))


def test_bom_line_from_calculator_line_carries_its_fields() -> None:
    line = BomLine(
        item=Item.POST_CONCRETE,
        name="Stup za betoniranje 125 cm",
        quantity=11,
        size="125 cm",
        unit_price_on_request=True,
        made_to_order=True,
        label="po narudžbi, 10–15 dana",
    )
    out = BomLineOut.from_line(line, unit_price=None)
    assert out.model_dump(mode="json") == {
        "item": "post_concrete",
        "name": "Stup za betoniranje 125 cm",
        "size": "125 cm",
        "quantity": 11,
        "unit_price": None,
        "line_total": None,
        "price_on_request": True,
        "made_to_order": True,
        "label": "po narudžbi, 10–15 dana",
    }


def test_bom_line_from_calculator_line_prices_it() -> None:
    line = BomLine(item=Item.PVC_CLIP, name="Obujmica PVC", quantity=44)
    out = BomLineOut.from_line(line, unit_price=Decimal("1.5"))
    assert (out.unit_price, out.line_total, out.price_on_request) == ("1.50", "66.00", False)


def test_on_request_calculator_line_never_shows_a_price() -> None:
    # fence-rules.md: an unconfirmed item shows "cijena na upit", even if a price exists.
    line = BomLine(item=Item.POST_CONCRETE, name="Stup", quantity=2, unit_price_on_request=True)
    out = BomLineOut.from_line(line, unit_price=Decimal("20"))
    assert (out.unit_price, out.line_total, out.price_on_request) == (None, None, True)


# --- error ------------------------------------------------------------------------


def test_rate_limited_error_carries_retry_after() -> None:
    error = ErrorEvent.model_validate(
        {"type": "error", "kind": "rate_limited", "retry_after_seconds": 30}
    )
    assert error.retry_after_seconds == 30


def test_retry_after_belongs_only_to_rate_limited() -> None:
    with pytest.raises(ValidationError, match="retry_after_seconds"):
        ErrorEvent.model_validate({"type": "error", "kind": "internal", "retry_after_seconds": 5})


# --- schema and docs --------------------------------------------------------------


def test_committed_json_schema_is_up_to_date() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == chat_event_schema(), (
        "docs/api/chat-events.schema.json is out of date: "
        "run `uv run python -m martom.api.chat_events` and commit the result"
    )


def test_schema_lists_every_event_type() -> None:
    mapping = chat_event_schema()["discriminator"]["mapping"]
    assert sorted(mapping) == sorted(EVENT_TYPES)


def docs_examples() -> list[dict[str, Any]]:
    text = DOCS_PATH.read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)]


def test_every_docs_example_is_a_valid_event() -> None:
    examples = docs_examples()
    for example in examples:
        parse_event(example)
    assert {example["type"] for example in examples} == set(EVENT_TYPES)
