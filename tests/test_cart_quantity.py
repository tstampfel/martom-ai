"""Cart quantities and prices only from tool output (#147; Tomislav, 28 Nov 2026)."""

# ruff: noqa: RUF001, RUF003

from decimal import Decimal
from typing import Any

import pytest

from martom.agent.cart_quantity import (
    CartQuantityError,
    bom_lines_for,
    bom_quantity,
    matches_bom,
    price_sources,
)
from martom.bom.roll import roll_fence_bom

PANEL_LINE: dict[str, Any] = {
    "source": "olx",
    "listing_id": 63996799,
    "manual_key": None,
    "title": "PANEL 3D OGRADA",
    "url": "https://martom.olx.ba/artikal/panel-3d-ograda-63996799",
    "variant_label": "153 × 250",
    "quantity": 10,
    "unit_price": "53.00",
}
ANCHOR_LINE: dict[str, Any] = {
    "source": "manual",
    "listing_id": None,
    "manual_key": "anchor_m8x70",
    "title": "Anker M8×70",
    "url": None,
    "variant_label": None,
    "quantity": 44,
    "unit_price": "0.80",
}


def bom_line(item: str, size: str | None, quantity: int, unit_price: str | None) -> dict[str, Any]:
    return {"item": item, "size": size, "quantity": quantity, "unit_price": unit_price}


BOM = {
    "lines": [
        bom_line("panel", "153 × 250 cm", 10, "53.00"),
        bom_line("post_base_plate", "155 cm", 11, None),
        bom_line("anchor_m8x70", "M8×70", 44, "0.80"),
    ]
}


def test_the_bom_line_of_the_variant_being_added_is_found() -> None:
    assert [line["quantity"] for line in bom_lines_for(PANEL_LINE, BOM)] == [10]


def test_another_variant_of_the_same_listing_is_not_that_line() -> None:
    # One listing holds panels and posts; the 153 × 250 line is not the 103 × 250 panel.
    assert bom_lines_for({**PANEL_LINE, "variant_label": "103 × 250"}, BOM) == []


def test_a_manual_product_matches_its_bom_line_by_key() -> None:
    assert [line["quantity"] for line in bom_lines_for(ANCHOR_LINE, BOM)] == [44]


def test_a_sizeless_line_of_the_same_listing_is_not_the_panel() -> None:
    # The PVC clips (no size) are priced from the same listing; the map pins them to the
    # "PVC kopča" variant, so a panel never takes the clips' 44.
    bom = {"lines": [bom_line("pvc_clip", None, 44, "0.30")]}
    assert bom_lines_for(PANEL_LINE, bom) == []


def test_nothing_matches_without_a_materials_list() -> None:
    assert bom_lines_for(PANEL_LINE, None) == []


def test_a_line_on_the_list_must_carry_its_quantity_and_price() -> None:
    assert matches_bom(PANEL_LINE, BOM)
    assert not matches_bom({**PANEL_LINE, "quantity": 153}, BOM)  # the height, not a count
    assert not matches_bom({**PANEL_LINE, "quantity": 25}, BOM)  # the length, not a count
    assert not matches_bom({**PANEL_LINE, "unit_price": "1.00"}, BOM)


def test_a_product_off_the_list_goes_in_as_one_piece() -> None:
    off_list = {**PANEL_LINE, "variant_label": "103 × 250"}
    assert matches_bom({**off_list, "quantity": 1}, BOM)
    assert not matches_bom({**off_list, "quantity": 37}, BOM)  # the reviewer's 37 x 53.00
    assert matches_bom({**PANEL_LINE, "quantity": 1}, None)


# Review R3-1: a roll fence's support T-posts are priced as the T-post of the same size,
# so the T-post's cart line takes both lines' quantities.

T_POST_LINE: dict[str, Any] = {
    "source": "olx",
    "listing_id": 59473597,
    "manual_key": None,
    "variant_label": "T stup 150 cm",
}
POST_SOURCE = {"item": "post_t", "size": "150 cm"}


def braced_bom(unit_price: str | None) -> dict[str, Any]:
    return {
        "lines": [
            bom_line("post_t", "150 cm", 26, unit_price),
            bom_line("brace_t_post", "150 cm", 4, unit_price),
        ],
        "price_sources": [POST_SOURCE, POST_SOURCE],
    }


def test_the_calculator_gives_a_brace_the_t_posts_price_source() -> None:
    bom = roll_fence_bom(
        sides_m=[Decimal(100)], roll_height_cm=100, post_model="t_post", closed=False
    )
    items = [line.item.value for line in bom.lines]
    sources = dict(zip(items, price_sources(bom.lines), strict=True))
    assert sources["brace_t_post"] == POST_SOURCE
    assert sources["post_t"] == POST_SOURCE


def test_posts_and_braces_of_one_t_post_add_up() -> None:
    assert bom_quantity(T_POST_LINE, braced_bom("12.50")) == (30, "12.50")
    assert matches_bom({**T_POST_LINE, "quantity": 30, "unit_price": "12.50"}, braced_bom("12.50"))
    assert not matches_bom(
        {**T_POST_LINE, "quantity": 26, "unit_price": "12.50"}, braced_bom("12.50")
    )


def test_on_a_draft_map_the_sum_comes_without_a_price() -> None:
    assert bom_quantity(T_POST_LINE, braced_bom(None)) == (30, None)


def test_lines_of_one_product_priced_differently_have_no_cart_quantity() -> None:
    bom = braced_bom("12.50")
    bom["lines"][1]["unit_price"] = None
    with pytest.raises(CartQuantityError):
        bom_quantity(T_POST_LINE, bom)
    assert not matches_bom({**T_POST_LINE, "quantity": 30, "unit_price": "12.50"}, bom)


def test_another_t_post_height_is_not_on_the_list() -> None:
    taller = {**T_POST_LINE, "variant_label": "T stup 175 cm"}
    assert bom_quantity(taller, braced_bom("12.50")) is None
