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
    CartEvent,
    CartLine,
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


def bom_event(lines: list[dict[str, Any]], total: str | None, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "bom",
        "title": "Popis materijala",
        "summary": "25 m · panel 153 cm · antracit",
        "lines": lines,
        "total": total,
        "unpriced_count": 0,
        "extra_lines": [],
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


def cart_line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "source": "olx",
        "listing_id": 63996799,
        "manual_key": None,
        "url": "https://martom.olx.ba/artikal/panelna-ograda-3d-63996799",
        "title": "Panelna ograda 3D (Nylofor)",
        "variant_label": "153 × 250",
        "quantity": 10,
        "unit_price": "53.00",
        "line_total": "530.00",
    }
    line.update(overrides)
    return line


MANUAL_CART_LINE = {
    "source": "manual",
    "listing_id": None,
    "manual_key": "anchor_m8x70",
    "url": None,
    "title": "Anker M8×70",
    "variant_label": None,
    "quantity": 4,
    "unit_price": "0.80",
    "line_total": "3.20",
}


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


# "٥٣.٠٠" is 53.00 in Arabic-Indic digits: Python's \d accepts it, the browser doesn't.
@pytest.mark.parametrize(
    "bad", [53.0, "53", "53.0", "53,00", "-1.00", "1e2", True, "\u0665\u0663.\u0660\u0660"]
)
def test_price_rejects_floats_and_badly_written_strings(bad: object) -> None:
    with pytest.raises(ValidationError):
        BomLineOut.model_validate(panel_line(quantity=1, unit_price=bad, line_total=bad))


def test_price_too_large_to_round_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="too large"):
        BomLineOut.model_validate(
            panel_line(quantity=1, unit_price=Decimal("1e30"), line_total=Decimal("1e30"))
        )


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
        bom_event([panel_line()], "530.00", unpriced_count=0),
        {"type": "bom_cleared"},
        {"type": "cart", "lines": [cart_line()]},
        {"type": "contact_martom"},
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
        "short_label": None,
        "badge": None,
        "note": None,
    }


def posts_question(**overrides: Any) -> dict[str, Any]:
    question: dict[str, Any] = {
        "id": "posts",
        "text": "Gdje idu stupovi?",
        "label": "Stupovi",
        "kind": "single",
        "options": [
            {"value": "plate", "label": "Na beton, s pločicom"},
            {"value": "concrete", "label": "U zemlju (betoniram)", "short_label": "U zemlju"},
        ],
        "default": "plate",
    }
    question.update(overrides)
    return question


def layout_question(**overrides: Any) -> dict[str, Any]:
    question: dict[str, Any] = {
        "id": "layout",
        "text": "Koliko ima kutova?",
        "label": "Kutovi",
        "kind": "layout",
        "options": [],
    }
    question.update(overrides)
    return question


def chips(questions: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "chips", "intro": None, "questions": questions}
    event.update(overrides)
    return event


def test_chips_carry_the_project_summary() -> None:
    event = parse_event(
        chips(
            [layout_question(), posts_question()],
            project={
                "title": "Ograda za dvorište",
                "length_m": 25,
                "facts": [{"label": "Visina", "value": "oko 150 cm"}],
            },
        )
    )
    dumped = event.model_dump(mode="json")
    assert dumped["project"] == {
        "title": "Ograda za dvorište",
        "length_m": 25,
        "facts": [{"label": "Visina", "value": "oko 150 cm"}],
        "panel_order": [],
    }
    assert dumped["questions"][1]["default"] == "plate"
    assert dumped["questions"][1]["label"] == "Stupovi"
    assert dumped["questions"][1]["options"][1]["short_label"] == "U zemlju"


def test_project_and_short_labels_are_optional() -> None:
    event = parse_event(chips([posts_question(label=None, default=None)]))
    dumped = event.model_dump(mode="json")
    assert dumped["project"] is None
    assert dumped["questions"][0]["label"] is None
    assert dumped["questions"][0]["default"] is None


@pytest.mark.parametrize("length_m", [0, -5])
def test_project_length_is_positive(length_m: float) -> None:
    with pytest.raises(ValidationError, match="length_m"):
        parse_event(chips([posts_question()], project={"title": "x", "length_m": length_m}))


def test_panel_order_lists_single_question_ids() -> None:
    color = posts_question(id="color", label="Boja", default=None)
    event = parse_event(
        chips(
            [layout_question(), posts_question(), color],
            project={"title": "x", "panel_order": ["color", "posts"]},
        )
    )
    assert event.model_dump(mode="json")["project"]["panel_order"] == ["color", "posts"]


@pytest.mark.parametrize("order", [["wood"], ["layout"], ["posts", "posts"]])
def test_panel_order_names_each_single_question_once(order: list[str]) -> None:
    with pytest.raises(ValidationError, match="panel_order"):
        parse_event(
            chips(
                [layout_question(), posts_question()],
                project={"title": "x", "panel_order": order},
            )
        )


def test_default_must_be_one_of_the_options() -> None:
    with pytest.raises(ValidationError, match="default"):
        parse_event(chips([posts_question(default="wood")]))


def test_layout_question_has_no_options_and_no_default() -> None:
    with pytest.raises(ValidationError, match="layout"):
        parse_event(chips([layout_question(options=[{"value": "2", "label": "2"}])]))
    with pytest.raises(ValidationError, match="default"):
        parse_event(chips([layout_question(default="2")]))


def test_at_most_one_layout_question() -> None:
    with pytest.raises(ValidationError, match="layout"):
        parse_event(chips([layout_question(), layout_question(id="layout2")]))


def test_question_ids_are_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        parse_event(chips([posts_question(), posts_question()]))


def test_side_lengths_kind_is_gone() -> None:
    # Replaced by the layout kind; the sides travel in the request, not as a question.
    with pytest.raises(ValidationError):
        parse_event(chips([layout_question(kind="side_lengths")]))


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


@pytest.mark.parametrize("listing_id", [0, -63996799])
def test_product_card_listing_id_is_positive(listing_id: int) -> None:
    with pytest.raises(ValidationError, match="listing_id"):
        ProductCard.model_validate(card(listing_id=listing_id))


def test_product_card_without_price_is_on_request() -> None:
    product = ProductCard.model_validate(card(price=None))
    assert product.price is None


def manual_card(**overrides: Any) -> dict[str, Any]:
    product = card(
        source="manual",
        listing_id=None,
        manual_key="anchor_m8x70",
        url=None,
        title="Anker M8×70",
        subtitle=None,
        price="0.80",
        price_unit="kom",
        variants=[],
    )
    product.update(overrides)
    return product


def test_olx_card_defaults_to_source_olx() -> None:
    product = ProductCard.model_validate(card())
    assert product.source == "olx"
    assert product.manual_key is None


def test_manual_product_card_has_no_listing_id_or_link() -> None:
    # #132: a manual_products.toml product has no OLX listing.
    product = ProductCard.model_validate(manual_card())
    assert product.listing_id is None
    assert product.url is None
    assert product.manual_key == "anchor_m8x70"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"url": "https://martom.olx.ba/artikal/anker-63996799"}, "url"),
        ({"listing_id": 63996799}, "listing_id"),
        ({"manual_key": None}, "manual_key"),
        ({"manual_key": ""}, "manual_key"),
    ],
)
def test_manual_product_card_never_carries_an_olx_listing(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProductCard.model_validate(manual_card(**overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"listing_id": None}, "listing_id"),
        ({"url": None}, "url"),
        ({"manual_key": "anchor_m8x70"}, "manual_key"),
    ],
)
def test_olx_card_needs_its_listing_and_link(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ProductCard.model_validate(card(**overrides))


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


def test_bom_line_item_must_be_a_bom_item() -> None:
    with pytest.raises(ValidationError, match="not a BOM item"):
        BomLineOut.model_validate(panel_line(item="gate"))


def test_bom_item_is_a_plain_string_in_the_schema() -> None:
    # Adding or removing a BOM item (e.g. #75) must not change the contract.
    item = chat_event_schema()["$defs"]["BomLineOut"]["properties"]["item"]
    assert item["type"] == "string"
    assert "enum" not in item and "$ref" not in item


def test_bom_quantity_is_at_least_one() -> None:
    with pytest.raises(ValidationError):
        BomLineOut.model_validate(panel_line(quantity=0, line_total="0.00"))


def brace_on_request() -> dict[str, Any]:
    return panel_line(
        item="brace_t_post",
        name="Potporni stup 100 cm",
        size="100 cm",
        quantity=2,
        unit_price=None,
        line_total=None,
        price_on_request=True,
    )


def test_bom_with_every_line_priced_has_a_total_and_no_unpriced_lines() -> None:
    event = BomEvent.model_validate(bom_event([panel_line()], "530.00", unpriced_count=0))
    assert (event.total, event.unpriced_count) == ("530.00", 0)


def test_bom_with_some_lines_on_request_totals_the_priced_lines() -> None:
    lines = [panel_line(), brace_on_request()]
    event = BomEvent.model_validate(bom_event(lines, "530.00", unpriced_count=1))
    assert (event.total, event.unpriced_count) == ("530.00", 1)


def test_bom_with_every_line_on_request_has_no_total() -> None:
    # Never a fake "0.00" (Tomislav, 26 Nov 2026): the UI shows "cijena na upit".
    event = BomEvent.model_validate(bom_event([brace_on_request()], None, unpriced_count=1))
    assert (event.total, event.unpriced_count) == (None, 1)


def test_bom_total_must_be_the_sum_of_priced_lines() -> None:
    with pytest.raises(ValidationError, match="total"):
        BomEvent.model_validate(bom_event([panel_line()], "531.00", unpriced_count=0))


@pytest.mark.parametrize(
    ("lines", "total", "unpriced_count"),
    [
        ([brace_on_request()], "0.00", 1),  # all na upit: total must be null
        ([panel_line()], None, 0),  # something priced: total must be set
    ],
)
def test_bom_total_is_null_exactly_when_every_line_is_on_request(
    lines: list[dict[str, Any]], total: str | None, unpriced_count: int
) -> None:
    with pytest.raises(ValidationError, match="total"):
        BomEvent.model_validate(bom_event(lines, total, unpriced_count=unpriced_count))


@pytest.mark.parametrize("unpriced_count", [0, 2, -1])
def test_bom_unpriced_count_is_the_number_of_on_request_lines(unpriced_count: int) -> None:
    lines = [panel_line(), brace_on_request()]
    with pytest.raises(ValidationError, match="unpriced_count"):
        BomEvent.model_validate(bom_event(lines, "530.00", unpriced_count=unpriced_count))


CONCRETE_POSTS = {
    "name": "Stupovi za betoniranje – po upitu",
    "note": "Rade se po narudžbi, 10–15 dana.",
}


def test_bom_extra_line_has_no_price_and_leaves_the_total_alone() -> None:
    # Tomislav, 26 Nov 2026: posts set in concrete are one no-price order line, never
    # a BOM line (#20, #27). It doesn't count as a "na upit" BOM line either.
    event = BomEvent.model_validate(
        bom_event([panel_line()], "530.00", unpriced_count=0, extra_lines=[CONCRETE_POSTS])
    )
    assert (event.total, event.unpriced_count) == ("530.00", 0)
    assert event.model_dump(mode="json")["extra_lines"] == [CONCRETE_POSTS]


def test_bom_extra_line_needs_a_name() -> None:
    with pytest.raises(ValidationError, match="name"):
        BomEvent.model_validate(
            bom_event([panel_line()], "530.00", extra_lines=[{"name": "", "note": None}])
        )


def test_bom_extra_line_cannot_carry_a_price() -> None:
    with pytest.raises(ValidationError, match="price"):
        BomEvent.model_validate(
            bom_event([panel_line()], "530.00", extra_lines=[{**CONCRETE_POSTS, "price": "1.00"}])
        )


def test_bom_line_from_calculator_line_carries_its_fields() -> None:
    line = BomLine(
        item=Item.BRACE_T_POST,
        name="Potporni stup 100 cm",
        quantity=4,
        size="100 cm",
        unit_price_on_request=True,
        made_to_order=True,
        label="po narudžbi, 10–15 dana",
    )
    out = BomLineOut.from_line(line, unit_price=None)
    assert out.model_dump(mode="json") == {
        "item": "brace_t_post",
        "name": "Potporni stup 100 cm",
        "size": "100 cm",
        "quantity": 4,
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


def test_calculator_line_with_sub_cent_price_rounds_before_multiplying() -> None:
    # A price read from OLX or computed per metre can carry more than two decimals.
    line = BomLine(item=Item.PVC_CLIP, name="Obujmica PVC", quantity=2)
    out = BomLineOut.from_line(line, unit_price=Decimal("1.005"))
    assert (out.unit_price, out.line_total) == ("1.01", "2.02")


@pytest.mark.parametrize("price", ["Infinity", "-1", "1e30"])
def test_calculator_line_with_an_impossible_price_raises_value_error(price: str) -> None:
    # A pydantic ValidationError is a ValueError too, so one except catches every case.
    line = BomLine(item=Item.PVC_CLIP, name="Obujmica PVC", quantity=2)
    with pytest.raises(ValueError, match="price"):
        BomLineOut.from_line(line, unit_price=Decimal(price))


def test_on_request_calculator_line_never_shows_a_price() -> None:
    # fence-rules.md: an unconfirmed item shows "cijena na upit", even if a price exists.
    line = BomLine(item=Item.BRACE_T_POST, name="Stup", quantity=2, unit_price_on_request=True)
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
    # The Request section above it has request bodies (tests/test_chat_request.py).
    text = DOCS_PATH.read_text(encoding="utf-8").split("\n## Events\n")[1]
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)]


def test_every_docs_example_is_a_valid_event() -> None:
    examples = docs_examples()
    for example in examples:
        parse_event(example)
    assert {example["type"] for example in examples} == set(EVENT_TYPES)


# --- cart, contact_martom, bom_cleared (#147) ---------------------------------------


def test_cart_line_total_is_unit_price_times_quantity() -> None:
    with pytest.raises(ValidationError, match="line_total"):
        CartLine.model_validate(cart_line(line_total="53.00"))


def test_cart_line_without_a_price_has_no_total() -> None:
    line = CartLine.model_validate(cart_line(unit_price=None, line_total=None))
    assert (line.unit_price, line.line_total) == (None, None)
    with pytest.raises(ValidationError, match="line_total"):
        CartLine.model_validate(cart_line(unit_price=None, line_total="530.00"))


def test_cart_quantity_is_at_least_one() -> None:
    with pytest.raises(ValidationError, match="quantity"):
        CartLine.model_validate(cart_line(quantity=0, line_total="0.00"))


def test_manual_cart_line_has_no_listing_or_link() -> None:
    assert CartLine.model_validate(MANUAL_CART_LINE).manual_key == "anchor_m8x70"
    with pytest.raises(ValidationError, match="url"):
        CartLine.model_validate(
            {**MANUAL_CART_LINE, "url": "https://martom.olx.ba/artikal/anker-1"}
        )
    with pytest.raises(ValidationError, match="listing_id"):
        CartLine.model_validate({**MANUAL_CART_LINE, "listing_id": 1})


def test_olx_cart_line_needs_its_listing_and_matching_link() -> None:
    with pytest.raises(ValidationError, match="listing_id"):
        CartLine.model_validate(cart_line(url="https://martom.olx.ba/artikal/panel-1"))
    with pytest.raises(ValidationError, match="manual_key"):
        CartLine.model_validate(cart_line(manual_key="anchor_m8x70"))


def test_cart_line_from_the_add_to_cart_tool_output() -> None:
    # agent/tools.py `_add_to_cart` returns this shape (no line_total): the total is
    # computed here, in Python, never by the model or the browser.
    tool_line = {key: value for key, value in MANUAL_CART_LINE.items() if key != "line_total"}
    assert CartLine.from_tool(tool_line).line_total == "3.20"
    no_price = {**tool_line, "unit_price": None}
    assert CartLine.from_tool(no_price).line_total is None


def test_cart_event_needs_a_line() -> None:
    with pytest.raises(ValidationError):
        CartEvent.model_validate({"type": "cart", "lines": []})


@pytest.mark.parametrize("event_type", ["bom_cleared", "contact_martom"])
def test_signal_events_carry_no_fields(event_type: str) -> None:
    assert parse_event({"type": event_type}).model_dump(mode="json") == {"type": event_type}
    with pytest.raises(ValidationError):
        parse_event({"type": event_type, "text": "x"})
