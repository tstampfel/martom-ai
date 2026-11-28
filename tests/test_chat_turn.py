"""The pure half of POST /api/chat: graph state and node updates -> chat events."""

# ruff: noqa: RUF001

import json
import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from martom.api import chat_copy
from martom.api.chat_events import (
    BomClearedEvent,
    BomEvent,
    CartEvent,
    ChipsEvent,
    ContactMartomEvent,
    ProductCardsEvent,
    StepEvent,
    TokenEvent,
)
from martom.api.chat_turn import (
    StepTracker,
    chunk_answer,
    final_events,
    parse_step_text,
    product_cards,
    search_step_text,
)

PANEL_OUTPUT = {
    "source": "olx",
    "listing_id": 63996799,
    "manual_key": None,
    "title": "PANEL 3D OGRADA",
    "url": "https://martom.olx.ba/artikal/panel-3d-ograda-63996799",
    "product_type": "panelna ograda",
    "colours": ["antracit"],
    "price": None,
    "variants": [
        {"label": "153 × 200", "price": None, "unit": "panel"},
        {"label": "153 × 250", "price": "53.00", "unit": "panel"},
    ],
}
ANCHOR_OUTPUT = {
    "source": "manual",
    "listing_id": None,
    "manual_key": "anchor_m8x70",
    "title": "Anker M8×70",
    "url": None,
    "product_type": None,
    "colours": [],
    "price": "0.80",
    "variants": [],
}
BOM = {
    "type": "bom",
    "title": "Popis materijala",
    "summary": "25 m · panel 153 cm",
    "lines": [
        {
            "item": "panel",
            "name": "Panel 153 × 250 cm",
            "size": "153 × 250 cm",
            "quantity": 10,
            "unit_price": None,
            "line_total": None,
            "price_on_request": True,
            "made_to_order": False,
            "label": None,
        }
    ],
    "total": None,
    "unpriced_count": 1,
    "extra_lines": [],
    "notes": [],
}


def tool_message(name: str, output: Any, *, status: str = "success") -> ToolMessage:
    content = json.dumps(output, ensure_ascii=False) if status == "success" else "error: x"
    return ToolMessage(content, tool_call_id=f"c-{name}", name=name, status=status)


def state(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage("ograda")],
        "answer": "Evo prijedloga.",
        "chips": None,
        "bom": None,
        "bom_changed": False,
    }
    return base | fields


# --- chunk_answer -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Jedna riječ",
        "Preporuka: 3D panelna ograda, 153 cm.\n\nČvrsta i uredna, ne treba je zatezati.",
        "  razmaci   na   početku i kraju  ",
        "- prva stavka\n- druga stavka\n- treća stavka s više riječi u njoj",
    ],
)
def test_chunks_join_back_to_the_exact_answer(text: str) -> None:
    assert "".join(chunk_answer(text)) == text


def test_a_long_answer_is_cut_into_several_chunks() -> None:
    chunks = chunk_answer(" ".join(f"riječ{i}" for i in range(40)))
    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)


def test_an_empty_answer_has_no_chunks() -> None:
    assert chunk_answer("") == []


# --- product_cards ------------------------------------------------------------------


def test_cards_come_from_this_turns_get_product_results() -> None:
    messages = [
        HumanMessage("ograda"),
        AIMessage("", tool_calls=[]),
        tool_message("get_product", ANCHOR_OUTPUT),  # an earlier turn: not shown again
        HumanMessage("panel 153"),
        tool_message("search_products", {"results": []}),
        tool_message("get_product", PANEL_OUTPUT),
    ]
    cards = product_cards(state(messages=messages))
    assert [card.listing_id for card in cards] == [63996799]
    card = cards[0]
    assert card.url == PANEL_OUTPUT["url"]
    assert card.why == ""
    # No listing price: the first priced variant, with its unit.
    assert (card.price, card.price_unit) == ("53.00", "panel")
    assert [(v.label, v.price) for v in card.variants] == [
        ("153 × 200", None),
        ("153 × 250", "53.00"),
    ]


def test_a_manual_product_card_has_no_link() -> None:
    cards = product_cards(
        state(messages=[HumanMessage("anker"), tool_message("get_product", ANCHOR_OUTPUT)])
    )
    assert len(cards) == 1
    assert (cards[0].source, cards[0].manual_key, cards[0].url) == ("manual", "anchor_m8x70", None)
    assert cards[0].price == "0.80"


def test_a_product_looked_up_twice_is_one_card_and_errors_are_skipped() -> None:
    messages = [
        HumanMessage("panel"),
        tool_message("get_product", PANEL_OUTPUT),
        tool_message("get_product", None, status="error"),
        tool_message("get_product", PANEL_OUTPUT),
    ]
    assert len(product_cards(state(messages=messages))) == 1


def test_a_listing_without_a_link_gets_no_card() -> None:
    no_slug = PANEL_OUTPUT | {"url": None}
    messages = [HumanMessage("panel"), tool_message("get_product", no_slug)]
    assert product_cards(state(messages=messages)) == []


# --- final_events -------------------------------------------------------------------


def test_an_answer_streams_as_tokens_and_ends_without_done() -> None:
    events = final_events(state(answer="Evo dva prijedloga za vašu ogradu."))
    assert all(isinstance(event, TokenEvent) for event in events)
    assert "".join(e.text for e in events if isinstance(e, TokenEvent)) == (
        "Evo dva prijedloga za vašu ogradu."
    )


def test_chips_follow_the_needs_input_step_and_the_text() -> None:
    chips = {
        "intro": "Još dvije stvari:",
        "project": None,
        "questions": [
            {"id": "layout", "text": "Koliko ima kutova?", "kind": "layout", "options": []}
        ],
    }
    events = final_events(state(answer="Trebam još podataka.", chips=chips))
    assert events[0] == StepEvent(id="clarify", text=chat_copy.STEP_CLARIFY, status="needs_input")
    assert isinstance(events[1], TokenEvent)
    assert isinstance(events[-1], ChipsEvent)
    assert events[-1].intro == "Još dvije stvari:"


def test_a_changed_bom_is_sent_after_the_cards() -> None:
    messages = [HumanMessage("ograda 25 m"), tool_message("get_product", PANEL_OUTPUT)]
    events = final_events(state(messages=messages, bom=BOM, bom_changed=True))
    kinds = [event.type for event in events]
    assert kinds[-2:] == ["product_cards", "bom"]
    assert isinstance(events[-2], ProductCardsEvent)
    assert isinstance(events[-1], BomEvent)
    assert events[-1].unpriced_count == 1


def test_an_unchanged_bom_is_not_sent_again() -> None:
    events = final_events(state(bom=BOM, bom_changed=False))
    assert "bom" not in [event.type for event in events]


def test_a_cleared_bom_tells_the_browser_to_drop_the_old_list() -> None:
    # Tomislav, 28 Nov 2026 (#147, launch blocker): no stale list or total on screen.
    events = final_events(state(bom=None, bom_changed=True))
    kinds = [event.type for event in events]
    assert "bom" not in kinds
    assert kinds.count("bom_cleared") == 1
    assert isinstance(events[0], BomClearedEvent)  # before the text, so it goes at once


def test_a_cleared_bom_is_sent_on_a_clarify_turn_too() -> None:
    # The fence changed and the advisor asks again: the old list must still go.
    chips = {
        "intro": None,
        "project": None,
        "questions": [
            {"id": "layout", "text": "Koliko ima kutova?", "kind": "layout", "options": []}
        ],
    }
    events = final_events(state(chips=chips, bom=None, bom_changed=True))
    kinds = [event.type for event in events]
    assert "bom_cleared" in kinds
    assert kinds[-1] == "chips"


def test_no_list_change_sends_no_cleared_event() -> None:
    assert "bom_cleared" not in [e.type for e in final_events(state(bom=None))]
    assert "bom_cleared" not in [e.type for e in final_events(state(bom=BOM, bom_changed=True))]


# A panel on the BOM above: the list's quantity (10) and price (null, "na upit").
CART_LINE = {
    "source": "olx",
    "listing_id": 63996799,
    "manual_key": None,
    "title": "PANEL 3D OGRADA",
    "url": "https://martom.olx.ba/artikal/panel-3d-ograda-63996799",
    "variant_label": "153 × 250",
    "quantity": 10,
    "unit_price": None,
}
# A product off the list: 1 piece at the catalogue price.
ANCHOR_CART_LINE = {
    "source": "manual",
    "listing_id": None,
    "manual_key": "anchor_m8x70",
    "title": "Anker M8×70",
    "url": None,
    "variant_label": None,
    "quantity": 1,
    "unit_price": "0.80",
}


def cart_quantities(**fields: Any) -> list[int]:
    events = final_events(state(**fields))
    return [line.quantity for e in events if isinstance(e, CartEvent) for line in e.lines]


def test_this_turns_cart_additions_are_one_cart_event_with_tool_prices() -> None:
    events = final_events(state(bom=BOM, cart=[CART_LINE, ANCHOR_CART_LINE]))
    carts = [event for event in events if isinstance(event, CartEvent)]
    assert len(carts) == 1
    assert [(line.quantity, line.unit_price, line.line_total) for line in carts[0].lines] == [
        (10, None, None),
        (1, "0.80", "0.80"),
    ]


def test_no_cart_additions_send_no_cart_event() -> None:
    assert "cart" not in [event.type for event in final_events(state(cart=[]))]
    assert "cart" not in [event.type for event in final_events(state())]


def test_a_cart_line_that_fails_the_contract_is_skipped() -> None:
    broken = {**ANCHOR_CART_LINE, "url": "https://olx.ba/artikal/63996799"}
    assert cart_quantities(cart=[broken, ANCHOR_CART_LINE]) == [1]


# Second guard (Tomislav, 28 Nov 2026): the cart is checked against the materials list,
# never against numbers in the chat.


def test_a_bom_product_must_carry_the_lists_quantity() -> None:
    assert cart_quantities(bom=BOM, cart=[CART_LINE]) == [10]
    assert cart_quantities(bom=BOM, cart=[{**CART_LINE, "quantity": 153}]) == []  # a height
    assert cart_quantities(bom=BOM, cart=[{**CART_LINE, "quantity": 25}]) == []  # a length


def test_a_bom_product_must_carry_the_lists_price() -> None:
    assert cart_quantities(bom=BOM, cart=[{**CART_LINE, "unit_price": "53.00"}]) == []


def test_a_product_off_the_list_is_one_piece() -> None:
    # The reviewer's repro: 37 x 53.00 never reaches the korpa.
    off_list = {**CART_LINE, "quantity": 37, "unit_price": "53.00"}
    assert cart_quantities(cart=[off_list]) == []
    assert cart_quantities(cart=[{**off_list, "quantity": 1}]) == [1]


@pytest.mark.parametrize("flag", ["fallback", "generate_failed"])
def test_a_fallback_turn_never_sends_a_cart(flag: str) -> None:
    assert cart_quantities(bom=BOM, cart=[CART_LINE], **{flag: True}) == []


def test_dropped_lines_are_logged_by_count_only(caplog: pytest.LogCaptureFixture) -> None:
    line = {**ANCHOR_CART_LINE, "quantity": 37}
    with caplog.at_level(logging.INFO, logger="martom.api"):
        final_events(state(cart=[line]))
    record = next(r for r in caplog.records if r.getMessage() == "cart lines dropped")
    assert record.ungrounded == 1  # type: ignore[attr-defined]
    assert all("37" not in r.getMessage() for r in caplog.records)


def test_contact_martom_is_offered_after_the_text() -> None:
    events = final_events(state(answer="Javite se Martomu.", contact_martom=True))
    assert isinstance(events[-1], ContactMartomEvent)
    assert "contact_martom" not in [e.type for e in final_events(state(contact_martom=False))]


def test_reply_order_is_cleared_tokens_cards_bom_cart_contact() -> None:
    messages = [HumanMessage("ograda"), tool_message("get_product", PANEL_OUTPUT)]
    events = final_events(
        state(messages=messages, bom=BOM, bom_changed=True, cart=[CART_LINE], contact_martom=True)
    )
    kinds = [event.type for event in events if event.type != "token"]
    assert kinds == ["product_cards", "bom", "cart", "contact_martom"]


# --- StepTracker --------------------------------------------------------------------


def test_steps_follow_the_graph_nodes() -> None:
    tracker = StepTracker()
    events = [tracker.start()]
    events += tracker.update("guardrail", {"route": "retrieve"})
    events += tracker.update("retrieve", {"hits": []})
    events += tracker.update("grade", {"grade": "ok"})
    events += tracker.update("generate", {})
    events += tracker.update("tools", {"bom": BOM, "bom_changed": True})
    events += tracker.update("generate", {})
    events += tracker.update("grounding", {"answer": "x"})
    assert [(e.id, e.status) for e in events] == [
        ("parse", "working"),
        ("parse", "done"),
        ("search", "working"),
        ("search", "done"),
        ("bom", "done"),
    ]
    assert events[0].text == chat_copy.STEP_PARSE


def test_a_clarify_turn_only_finishes_the_parse_step() -> None:
    tracker = StepTracker()
    events = [tracker.start(), *tracker.update("guardrail", {"route": "clarify"})]
    events += tracker.update("clarify", {"answer": "x"})
    assert [(e.id, e.status) for e in events] == [("parse", "working"), ("parse", "done")]


def test_a_rewrite_loop_keeps_one_search_step() -> None:
    tracker = StepTracker()
    events = [tracker.start(), *tracker.update("guardrail", {"route": "retrieve"})]
    for node in ("retrieve", "grade", "rewrite", "retrieve", "grade", "generate", "grounding"):
        events += tracker.update(node, {})
    assert [(e.id, e.status) for e in events].count(("search", "done")) == 1


# --- dynamic step texts (Tomislav, 28 Nov 2026) ---------------------------------------


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (1, "Pronađen 1 proizvod"),
        (2, "Pronađena 2 proizvoda"),
        (3, "Pronađena 3 proizvoda"),
        (4, "Pronađena 4 proizvoda"),
        (5, "Pronađeno 5 proizvoda"),
        (10, "Pronađeno 10 proizvoda"),
        (11, "Pronađeno 11 proizvoda"),
        (12, "Pronađeno 12 proizvoda"),
        (13, "Pronađeno 13 proizvoda"),
        (14, "Pronađeno 14 proizvoda"),
        (21, "Pronađen 21 proizvod"),
        (22, "Pronađena 22 proizvoda"),
        (25, "Pronađeno 25 proizvoda"),
        (111, "Pronađeno 111 proizvoda"),
        (112, "Pronađeno 112 proizvoda"),
        (101, "Pronađen 101 proizvod"),
    ],
)
def test_the_search_step_counts_products_in_correct_bosnian(count: int, text: str) -> None:
    assert search_step_text(count) == text


def test_no_products_keeps_the_fixed_search_text() -> None:
    assert search_step_text(0) == chat_copy.STEP_SEARCH


def test_the_parse_step_shows_what_python_parsed_from_the_fence() -> None:
    project = {"kind": "panel", "sides_m": [10, 5, 10], "height_cm": 150, "colour": "zlatna"}
    # The colour is text the model copied, so it is never shown here.
    assert parse_step_text(project) == "Razumijem: panelna ograda · 25 m · 150 cm"


def test_the_parse_step_uses_the_single_length_without_sides() -> None:
    project = {"kind": None, "sides_m": [], "total_length_m": 25.5, "height_cm": None}
    assert parse_step_text(project) == "Razumijem: 25,5 m"


@pytest.mark.parametrize("project", [None, {}, {"kind": None, "sides_m": [], "height_cm": None}])
def test_nothing_parsed_keeps_the_fixed_parse_text(project: dict[str, Any] | None) -> None:
    assert parse_step_text(project) == chat_copy.STEP_PARSE


def test_steps_carry_the_parsed_fence_and_the_relevant_count() -> None:
    tracker = StepTracker()
    tracker.start()
    project = {"kind": "roll", "sides_m": [], "total_length_m": 30, "height_cm": 125}
    parse = tracker.update("guardrail", {"route": "retrieve", "project": project})
    assert parse[0].text == "Razumijem: ograda u roli · 30 m · 125 cm"
    tracker.update("retrieve", {"hits": [{}] * 10})
    # The grade's count of relevant hits, not the 10 retrieved (review R2-1).
    tracker.update("grade", {"grade": "ok", "relevant_hits": 3})
    done = tracker.update("generate", {})
    assert done[0].text == "Pronađena 3 proizvoda"


def search_done_text(*updates: tuple[str, dict[str, Any]]) -> str:
    tracker = StepTracker()
    tracker.start()
    tracker.update("guardrail", {"route": "retrieve"})
    for node, changes in updates:
        tracker.update(node, changes)
    done = [e for e in tracker.update("generate", {}) if e.id == "search"]
    return done[0].text


def test_a_weak_search_keeps_the_fixed_text_however_many_hits_came_back() -> None:
    text = search_done_text(
        ("retrieve", {"hits": [{}] * 10}),
        ("grade", {"grade": "weak", "relevant_hits": 0}),
    )
    assert text == chat_copy.STEP_SEARCH


def test_a_weak_grade_never_shows_a_count_even_if_one_is_given() -> None:
    text = search_done_text(("grade", {"grade": "weak", "relevant_hits": 4}))
    assert text == chat_copy.STEP_SEARCH


def test_after_a_rewrite_the_latest_grade_counts() -> None:
    text = search_done_text(
        ("retrieve", {"hits": [{}] * 10}),
        ("grade", {"grade": "weak", "relevant_hits": 0}),
        ("rewrite", {"query": "ograda"}),
        ("retrieve", {"hits": [{}] * 10}),
        ("grade", {"grade": "ok", "relevant_hits": 4}),
    )
    assert text == "Pronađena 4 proizvoda"


def test_a_rewrite_that_stays_weak_keeps_the_fixed_text() -> None:
    text = search_done_text(
        ("grade", {"grade": "ok", "relevant_hits": 5}),
        ("rewrite", {"query": "x"}),  # not possible after "ok"; the last grade decides
        ("grade", {"grade": "weak", "relevant_hits": 0}),
    )
    assert text == chat_copy.STEP_SEARCH


# --- R1-9: the unit of a listing price ----------------------------------------------


def test_a_listing_price_takes_the_unit_of_its_matching_variant() -> None:
    output = PANEL_OUTPUT | {
        "price": "53.00",
        "variants": [
            {"label": "153 × 200", "price": "40.00", "unit": "kom"},
            {"label": "153 × 250", "price": "53.00", "unit": "panel"},
        ],
    }
    cards = product_cards(state(messages=[HumanMessage("p"), tool_message("get_product", output)]))
    assert (cards[0].price, cards[0].price_unit) == ("53.00", "panel")


def test_a_listing_price_without_a_matching_variant_has_no_unit() -> None:
    output = PANEL_OUTPUT | {"price": "60.00"}
    cards = product_cards(state(messages=[HumanMessage("p"), tool_message("get_product", output)]))
    assert (cards[0].price, cards[0].price_unit) == ("60.00", None)
