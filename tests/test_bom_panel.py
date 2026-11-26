"""Panel fence BOM: quantities follow docs/domain/fence-rules.md (Toni, 26 Nov 2026)."""

# The labels under test use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

import json
from decimal import Decimal
from pathlib import Path

import pytest

from martom.bom.panel import (
    PANEL_RULES,
    BomLine,
    Item,
    PostType,
    panel_fence_bom,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bom"


def quantities(lines: list[BomLine]) -> dict[Item, int]:
    return {line.item: line.quantity for line in lines}


def line_for(lines: list[BomLine], item: Item) -> BomLine:
    matches = [line for line in lines if line.item == item]
    assert len(matches) == 1, f"expected exactly one {item} line, got {matches}"
    return matches[0]


# --- the 905,10 KM reference case -------------------------------------------------------


def test_reference_case_25m_153cm_totals_905_10_km_with_recorded_olx_prices() -> None:
    fixture = json.loads((FIXTURES / "panel_25m_153.json").read_text(encoding="utf-8"))
    given = fixture["input"]

    lines = panel_fence_bom(
        length_m=Decimal(given["length_m"]),
        panel_height_cm=given["panel_height_cm"],
        panel_width_cm=given["panel_width_cm"],
        corners=given["corners"],
        gate=given["gate"],
        post_type=PostType(given["post_type"]),
    )

    expected = {Item(key): qty for key, qty in fixture["expected_quantities"].items()}
    assert quantities(lines) == expected

    # Prices live in the fixture, never in runtime code; the BOM only gives quantities.
    prices = {Item(key): Decimal(value) for key, value in fixture["unit_prices_km"].items()}
    total = sum((prices[line.item] * line.quantity for line in lines), Decimal("0"))
    assert total == Decimal(fixture["expected_total_km"])


def test_reference_case_lines_name_the_right_sizes() -> None:
    lines = panel_fence_bom(length_m=Decimal(25), panel_height_cm=153)

    assert line_for(lines, Item.PANEL).size == "153 × 250 cm"
    assert line_for(lines, Item.POST_BASE_PLATE).size == "155 cm"
    assert all(line.unit_price_on_request is False for line in lines)
    assert all(line.made_to_order is False for line in lines)


# --- lengths: 10 m and partial panels ---------------------------------------------------


def test_10m_with_250cm_panels_is_4_panels_and_5_posts() -> None:
    lines = panel_fence_bom(length_m=Decimal(10), panel_height_cm=153)

    assert quantities(lines) == {
        Item.PANEL: 4,
        Item.POST_BASE_PLATE: 5,
        Item.PVC_CLIP: 20,
        Item.ANCHOR_M8X80: 20,
    }


def test_10m_with_200cm_panels_is_5_panels_and_6_posts() -> None:
    lines = panel_fence_bom(length_m=Decimal(10), panel_height_cm=103, panel_width_cm=200)

    assert quantities(lines) == {
        Item.PANEL: 5,
        Item.POST_BASE_PLATE: 6,
        Item.PVC_CLIP: 18,
        Item.ANCHOR_M8X80: 24,
    }
    assert line_for(lines, Item.PANEL).size == "103 × 200 cm"
    assert line_for(lines, Item.POST_BASE_PLATE).size == "105 cm"


@pytest.mark.parametrize(
    ("length_m", "expected_panels"),
    [
        (Decimal("10.3"), 5),  # 4 full panels + a partial one, which is a whole panel cut down
        (Decimal("0.5"), 1),
        (Decimal("2.5"), 1),  # exactly one panel, no extra
        (Decimal("2.51"), 2),
        (Decimal("24.99"), 10),
    ],
)
def test_a_partial_panel_rounds_up_to_a_whole_panel(
    length_m: Decimal, expected_panels: int
) -> None:
    lines = panel_fence_bom(length_m=length_m, panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == expected_panels
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == expected_panels + 1


def test_float_lengths_do_not_add_a_panel_through_rounding_error() -> None:
    # 0.1 + 0.2 is 0.30000000000000004 as a float; 7.5 m must still be exactly 3 panels.
    lines = panel_fence_bom(length_m=7.5, panel_height_cm=153)
    assert line_for(lines, Item.PANEL).quantity == 3

    lines = panel_fence_bom(length_m=0.1 + 0.2 + 4.7, panel_height_cm=153)  # "5.0" m
    assert line_for(lines, Item.PANEL).quantity == 2


# --- clips and posts per height ---------------------------------------------------------


@pytest.mark.parametrize(
    ("height", "post_cm", "clips_per_post"),
    [(83, 85, 3), (103, 105, 3), (123, 125, 3), (153, 155, 4), (203, 205, 5)],
)
def test_rules_table_matches_fence_rules_md(height: int, post_cm: int, clips_per_post: int) -> None:
    rule = PANEL_RULES[height]
    assert rule.post_height_cm == post_cm
    assert rule.clips_per_post == clips_per_post


def test_rules_table_has_only_the_heights_toni_confirmed() -> None:
    assert sorted(PANEL_RULES) == [83, 103, 123, 153, 203]


def test_203cm_panels_use_5_clips_and_4_anchors_per_post() -> None:
    lines = panel_fence_bom(length_m=Decimal(10), panel_height_cm=203)

    assert line_for(lines, Item.PVC_CLIP).quantity == 5 * 5
    assert line_for(lines, Item.ANCHOR_M8X80).quantity == 5 * 4


# --- corners ----------------------------------------------------------------------------


def test_two_corners_add_8_clips_and_no_posts_or_anchors() -> None:
    straight = quantities(panel_fence_bom(length_m=Decimal(25), panel_height_cm=153))
    cornered = quantities(panel_fence_bom(length_m=Decimal(25), panel_height_cm=153, corners=2))

    assert cornered[Item.PVC_CLIP] == straight[Item.PVC_CLIP] + 8 == 52
    assert cornered[Item.POST_BASE_PLATE] == straight[Item.POST_BASE_PLATE]
    assert cornered[Item.ANCHOR_M8X80] == straight[Item.ANCHOR_M8X80]
    assert cornered[Item.PANEL] == straight[Item.PANEL]


@pytest.mark.parametrize(("height", "extra_per_corner"), [(83, 3), (123, 3), (153, 4), (203, 5)])
def test_each_corner_post_gets_double_clips(height: int, extra_per_corner: int) -> None:
    straight = quantities(panel_fence_bom(length_m=Decimal(20), panel_height_cm=height))
    cornered = quantities(panel_fence_bom(length_m=Decimal(20), panel_height_cm=height, corners=1))

    assert cornered[Item.PVC_CLIP] - straight[Item.PVC_CLIP] == extra_per_corner


def test_corners_must_sit_on_inner_posts() -> None:
    # 5 m = 2 panels = 3 posts: only the middle post can be a corner.
    assert panel_fence_bom(length_m=Decimal(5), panel_height_cm=153, corners=1)
    with pytest.raises(ValueError, match="corner"):
        panel_fence_bom(length_m=Decimal(5), panel_height_cm=153, corners=2)


# --- concrete-in-soil posts: "po narudžbi" ----------------------------------------------


def test_concrete_posts_are_one_made_to_order_line_with_no_price() -> None:
    lines = panel_fence_bom(
        length_m=Decimal(25), panel_height_cm=153, post_type=PostType.CONCRETE_IN_SOIL
    )

    post = line_for(lines, Item.POST_CONCRETE)
    assert post.quantity == 11
    assert post.made_to_order is True
    assert post.unit_price_on_request is True
    assert "po narudžbi" in post.name
    assert [line for line in lines if line.made_to_order] == [post]
    assert Item.POST_BASE_PLATE not in quantities(lines)


def test_concrete_posts_keep_clips_but_need_no_anchors() -> None:
    lines = panel_fence_bom(
        length_m=Decimal(25), panel_height_cm=153, post_type=PostType.CONCRETE_IN_SOIL
    )

    assert line_for(lines, Item.PVC_CLIP).quantity == 44
    assert Item.ANCHOR_M8X80 not in quantities(lines)


def test_base_plate_posts_are_the_default() -> None:
    lines = panel_fence_bom(length_m=Decimal(25), panel_height_cm=153)
    assert Item.POST_BASE_PLATE in quantities(lines)
    assert Item.POST_CONCRETE not in quantities(lines)


# --- gate -------------------------------------------------------------------------------


def test_a_gate_adds_one_price_on_request_line_and_changes_nothing_else() -> None:
    without = panel_fence_bom(length_m=Decimal(25), panel_height_cm=153)
    with_gate = panel_fence_bom(length_m=Decimal(25), panel_height_cm=153, gate=True)

    gate = line_for(with_gate, Item.GATE)
    assert gate.name == "Kapija – cijena na upit"
    assert gate.quantity == 1
    assert gate.unit_price_on_request is True
    assert [line for line in with_gate if line.item != Item.GATE] == without


def test_no_gate_line_by_default() -> None:
    lines = panel_fence_bom(length_m=Decimal(25), panel_height_cm=153)
    assert Item.GATE not in quantities(lines)


# --- invalid input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"length_m": Decimal(0)}, "length"),
        ({"length_m": Decimal(-3)}, "length"),
        ({"panel_height_cm": 100}, "height"),
        ({"panel_width_cm": 300}, "width"),
        ({"corners": -1}, "corner"),
    ],
)
def test_invalid_input_raises_value_error(kwargs: dict, message: str) -> None:
    arguments = {"length_m": Decimal(10), "panel_height_cm": 153, **kwargs}
    with pytest.raises(ValueError, match=message):
        panel_fence_bom(**arguments)
