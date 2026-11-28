"""The pure half of POST /api/chat: graph state and node updates -> chat events."""

# ruff: noqa: RUF001

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from martom.api import chat_copy
from martom.api.chat_events import (
    BomEvent,
    ChipsEvent,
    ProductCardsEvent,
    StepEvent,
    TokenEvent,
)
from martom.api.chat_turn import StepTracker, chunk_answer, final_events, product_cards

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


def test_a_cleared_bom_sends_nothing() -> None:
    events = final_events(state(bom=None, bom_changed=True))
    assert "bom" not in [event.type for event in events]


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
