"""Cart quantities and prices only from tool output (#147; Tomislav, 28 Nov 2026)."""

# ruff: noqa: RUF001, RUF003

from typing import Any

from martom.agent.cart_quantity import bom_lines_for, matches_bom

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
