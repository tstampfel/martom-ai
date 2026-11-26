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
    PriceRef,
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
        sides_m=[Decimal(side) for side in given["sides_m"]],
        panel_height_cm=given["panel_height_cm"],
        panel_width_cm=given["panel_width_cm"],
        closed=given["closed"],
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
    lines = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)

    assert line_for(lines, Item.PANEL).size == "153 × 250 cm"
    assert line_for(lines, Item.POST_BASE_PLATE).size == "155 cm"
    assert all(line.unit_price_on_request is False for line in lines)
    assert all(line.made_to_order is False for line in lines)


# --- lengths: 10 m and partial panels ---------------------------------------------------


def test_10m_with_250cm_panels_is_4_panels_and_5_posts() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(10)], panel_height_cm=153)

    assert quantities(lines) == {
        Item.PANEL: 4,
        Item.POST_BASE_PLATE: 5,
        Item.PVC_CLIP: 20,
        Item.ANCHOR_M8X80: 20,
    }


def test_10m_with_200cm_panels_is_5_panels_and_6_posts() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(10)], panel_height_cm=103, panel_width_cm=200)

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
    lines = panel_fence_bom(sides_m=[length_m], panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == expected_panels
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == expected_panels + 1


@pytest.mark.parametrize(
    ("side_m", "expected_panels"),
    [
        (5.000000000000001, 2),  # float noise just above 2 whole panels
        (1.1 * 50, 22),  # computes to 55.00000000000001, not 55.0
    ],
)
def test_float_noise_does_not_add_a_panel(side_m: float, expected_panels: int) -> None:
    # The side is rounded to whole centimetres before the panels are counted.
    assert side_m != expected_panels * 2.5  # the input really carries float noise

    lines = panel_fence_bom(sides_m=[side_m], panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == expected_panels
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == expected_panels + 1


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
    lines = panel_fence_bom(sides_m=[Decimal(10)], panel_height_cm=203)

    assert line_for(lines, Item.PVC_CLIP).quantity == 5 * 5
    assert line_for(lines, Item.ANCHOR_M8X80).quantity == 5 * 4


# --- sides and corners -----------------------------------------------------------------
# A corner is one post shared by two sides (fence-rules.md, "Corners"). Each side gets its
# own whole number of panels, so partial panels on different sides never merge.


def test_two_corners_add_8_clips_and_no_posts_or_anchors() -> None:
    # 25 m straight vs the same 25 m in three sides that are whole numbers of panels.
    straight = quantities(panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153))
    cornered = quantities(
        panel_fence_bom(sides_m=[Decimal(10), Decimal(10), Decimal(5)], panel_height_cm=153)
    )

    assert cornered[Item.PVC_CLIP] == straight[Item.PVC_CLIP] + 8 == 52
    assert cornered[Item.POST_BASE_PLATE] == straight[Item.POST_BASE_PLATE] == 11
    assert cornered[Item.ANCHOR_M8X80] == straight[Item.ANCHOR_M8X80] == 44
    assert cornered[Item.PANEL] == straight[Item.PANEL] == 10


@pytest.mark.parametrize(("height", "extra_per_corner"), [(83, 3), (123, 3), (153, 4), (203, 5)])
def test_each_corner_post_gets_double_clips(height: int, extra_per_corner: int) -> None:
    straight = quantities(panel_fence_bom(sides_m=[Decimal(20)], panel_height_cm=height))
    cornered = quantities(
        panel_fence_bom(sides_m=[Decimal(10), Decimal(10)], panel_height_cm=height)
    )

    assert cornered[Item.PVC_CLIP] - straight[Item.PVC_CLIP] == extra_per_corner


def test_partial_panels_on_two_sides_are_counted_per_side() -> None:
    # 10,3 m = 5 panels (6 posts), 9,7 m = 4 panels (5 posts), minus 1 shared corner post.
    lines = panel_fence_bom(sides_m=[Decimal("10.3"), Decimal("9.7")], panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == 9
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == 10
    assert line_for(lines, Item.PVC_CLIP).quantity == 10 * 4 + 1 * 4
    assert line_for(lines, Item.ANCHOR_M8X80).quantity == 10 * 4


def test_three_sides_of_5_1m_with_2_corners_need_9_panels() -> None:
    lines = panel_fence_bom(sides_m=[Decimal("5.1")] * 3, panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == 9  # 3 per side
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == 3 * 4 - 2


def test_a_closed_40m_square_has_as_many_posts_as_panels() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(10)] * 4, closed=True, panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == 16
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == 16
    # 16 posts, 4 of them corners with double clips.
    assert line_for(lines, Item.PVC_CLIP).quantity == 16 * 4 + 4 * 4
    assert line_for(lines, Item.ANCHOR_M8X80).quantity == 16 * 4


def test_sides_can_be_any_sequence_of_numbers() -> None:
    from_tuple = panel_fence_bom(sides_m=(10.3, 9.7), panel_height_cm=153)
    from_list = panel_fence_bom(sides_m=[Decimal("10.3"), Decimal("9.7")], panel_height_cm=153)
    assert from_tuple == from_list


# --- concrete-in-soil posts: "po narudžbi, 10–15 dana" ---------------------------------
# Rules: fence-rules.md, "Concrete-in-soil posts" (Toni, 26 Nov 2026, docs PR #50).


def test_103cm_concrete_posts_are_125cm_priced_as_the_105cm_base_plate_post() -> None:
    fixture = json.loads((FIXTURES / "panel_10m_103_concrete.json").read_text(encoding="utf-8"))
    given = fixture["input"]

    lines = panel_fence_bom(
        sides_m=[Decimal(side) for side in given["sides_m"]],
        panel_height_cm=given["panel_height_cm"],
        post_type=PostType(given["post_type"]),
    )

    post = line_for(lines, Item.POST_CONCRETE)
    assert post.quantity == fixture["expected_posts"]
    assert post.size == "125 cm"
    assert post.label == "po narudžbi, 10–15 dana"
    assert post.made_to_order is True
    assert post.unit_price_on_request is False
    # The line has no price of its own: it points at the base-plate post 105 cm listing.
    assert post.price_from == PriceRef(item=Item.POST_BASE_PLATE, size="105 cm")

    prices = {
        PriceRef(item=Item(row["item"]), size=row["size"]): Decimal(row["price_km"])
        for row in fixture["unit_prices_km"]
    }
    assert prices[post.price_from] == Decimal(fixture["expected_post_unit_price_km"])
    assert prices[post.price_from] * post.quantity == Decimal(fixture["expected_posts_total_km"])

    assert Item.ANCHOR_M8X80 not in quantities(lines)


@pytest.mark.parametrize("height", [83, 123, 153, 203])
def test_concrete_posts_for_unconfirmed_heights_have_no_size_and_no_price(height: int) -> None:
    lines = panel_fence_bom(
        sides_m=[Decimal(25)], panel_height_cm=height, post_type=PostType.CONCRETE_IN_SOIL
    )

    post = line_for(lines, Item.POST_CONCRETE)
    assert post.quantity == 11
    assert post.size is None
    assert post.price_from is None
    assert post.unit_price_on_request is True
    assert post.label == "po narudžbi, 10–15 dana"
    assert post.made_to_order is True


def test_concrete_posts_are_the_only_made_to_order_line() -> None:
    lines = panel_fence_bom(
        sides_m=[Decimal(25)], panel_height_cm=153, post_type=PostType.CONCRETE_IN_SOIL
    )

    assert [line.item for line in lines if line.made_to_order] == [Item.POST_CONCRETE]
    assert Item.POST_BASE_PLATE not in quantities(lines)


@pytest.mark.parametrize("height", [83, 103, 123, 153, 203])
def test_concrete_posts_keep_clips_but_need_no_anchors_and_no_concrete(height: int) -> None:
    base_plate = quantities(panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=height))
    lines = panel_fence_bom(
        sides_m=[Decimal(25)], panel_height_cm=height, post_type=PostType.CONCRETE_IN_SOIL
    )

    # Martom sells no concrete, so the list is panels, posts and clips only.
    assert [line.item for line in lines] == [Item.PANEL, Item.POST_CONCRETE, Item.PVC_CLIP]
    assert line_for(lines, Item.PVC_CLIP).quantity == base_plate[Item.PVC_CLIP]


@pytest.mark.parametrize(
    ("height", "concrete_post_cm"), [(83, None), (103, 125), (123, None), (153, None), (203, None)]
)
def test_rules_table_has_a_concrete_post_size_only_where_toni_confirmed_it(
    height: int, concrete_post_cm: int | None
) -> None:
    assert PANEL_RULES[height].concrete_post_height_cm == concrete_post_cm


def test_base_plate_posts_are_the_default() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)
    assert Item.POST_BASE_PLATE in quantities(lines)
    assert Item.POST_CONCRETE not in quantities(lines)
    assert all(line.label is None and line.price_from is None for line in lines)


def test_post_type_given_as_a_plain_string_is_accepted() -> None:
    # The agent's tool call (M4-3) passes JSON strings, not enum members.
    from_string = panel_fence_bom(sides_m=[25], panel_height_cm=153, post_type="base_plate")
    from_enum = panel_fence_bom(sides_m=[25], panel_height_cm=153, post_type=PostType.BASE_PLATE)
    assert from_string == from_enum

    concrete = panel_fence_bom(sides_m=[25], panel_height_cm=153, post_type="concrete_in_soil")
    assert Item.POST_CONCRETE in quantities(concrete)


# --- gate -------------------------------------------------------------------------------


def test_a_gate_adds_one_price_on_request_line_and_changes_nothing_else() -> None:
    without = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)
    with_gate = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153, gate=True)

    gate = line_for(with_gate, Item.GATE)
    assert gate.name == "Kapija – cijena na upit"
    assert gate.quantity == 1
    assert gate.unit_price_on_request is True
    assert [line for line in with_gate if line.item != Item.GATE] == without


def test_no_gate_line_by_default() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)
    assert Item.GATE not in quantities(lines)


# --- invalid input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sides_m": []}, "side"),
        ({"sides_m": [Decimal(0)]}, "side"),
        ({"sides_m": [Decimal(10), Decimal(-3)]}, "side"),
        ({"sides_m": [float("nan")]}, "side"),
        ({"sides_m": [float("inf")]}, "side"),
        ({"sides_m": ["abc"]}, "side"),
        ({"sides_m": ["25m"]}, "side"),
        ({"sides_m": [None]}, "side"),
        ({"sides_m": [True]}, "side"),
        ({"sides_m": Decimal(10)}, "side"),  # a number, not a list of sides
        ({"sides_m": "25"}, "side"),  # a string is a sequence too, but not of sides
        ({"sides_m": None}, "side"),
        ({"sides_m": [Decimal(10)] * 2, "closed": True}, "closed"),
        ({"closed": "yes"}, "closed"),
        ({"sides_m": ["1e27"]}, "side"),
        ({"sides_m": [10**30]}, "side"),
        ({"gate": "false"}, "gate"),
        ({"gate": 1}, "gate"),
        ({"panel_height_cm": 100}, "height"),
        ({"panel_height_cm": 153.0}, "height"),
        ({"panel_height_cm": "153"}, "height"),
        ({"panel_width_cm": 300}, "width"),
        ({"panel_width_cm": 250.0}, "width"),
        ({"post_type": "nonsense"}, "post type"),
    ],
)
def test_invalid_input_raises_value_error(kwargs: dict, message: str) -> None:
    arguments = {"sides_m": [Decimal(10)], "panel_height_cm": 153, **kwargs}
    with pytest.raises(ValueError, match=message):
        panel_fence_bom(**arguments)
