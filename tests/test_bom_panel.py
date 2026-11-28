"""Panel fence BOM: quantities follow docs/domain/fence-rules.md (Toni, 26 Nov 2026)."""

# The labels under test use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

import json
from decimal import Decimal
from pathlib import Path

import pytest

from martom.bom.panel import (
    MAX_SIDE_M,
    MAX_TOTAL_M,
    PANEL_RULES,
    BomLine,
    FenceLimit,
    FenceTooLargeError,
    Item,
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
# A corner is one post shared by two sides (fence-rules.md, "Corners (kutovi)"). Each side gets its
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


# --- posts: always base-plate posts ----------------------------------------------------
# Toni, 26 Nov 2026 14:52: concrete-in-soil panel posts are dropped from the BOM (nobody
# orders them); the advisor answers them as "po upitu". fence-rules.md keeps his size
# mapping as reference only.


def test_posts_are_always_base_plate_posts_with_anchors() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)
    assert [line.item for line in lines] == [
        Item.PANEL,
        Item.POST_BASE_PLATE,
        Item.PVC_CLIP,
        Item.ANCHOR_M8X80,
    ]
    assert all(line.label is None and line.price_from is None for line in lines)


def test_the_bom_takes_no_post_type_input() -> None:
    with pytest.raises(TypeError, match="post_type"):
        panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153, post_type="concrete_in_soil")  # type: ignore[call-arg]


def test_there_is_no_concrete_post_item() -> None:
    assert "post_concrete" not in {item.value for item in Item}


# --- gates are out of scope --------------------------------------------------------------
# Toni, 26 Nov 2026: gates are not part of the BOM; the advisor sends gate questions to
# Martom directly (M4-3).


def test_the_bom_takes_no_gate_input() -> None:
    with pytest.raises(TypeError, match="gate"):
        panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153, gate=True)  # type: ignore[call-arg]


def test_no_bom_line_is_a_gate() -> None:
    assert "gate" not in {item.value for item in Item}
    lines = panel_fence_bom(sides_m=[Decimal(25)], panel_height_cm=153)
    assert all("kapij" not in line.name.lower() for line in lines)


# --- length limits ----------------------------------------------------------------------
# Tomislav, 26 Nov 2026: a side over 300 m or a total over 1000 m gets no BOM; the advisor
# sends the customer to Martom directly. Both limits are inclusive.


def test_limits_are_300m_per_side_and_1000m_in_total() -> None:
    assert MAX_SIDE_M == 300
    assert MAX_TOTAL_M == 1000


@pytest.mark.parametrize("side_m", [Decimal(0), Decimal(-5)])
def test_a_side_of_zero_or_less_is_invalid_input_not_too_large(side_m: Decimal) -> None:
    with pytest.raises(ValueError, match="side") as error:
        panel_fence_bom(sides_m=[side_m], panel_height_cm=153)
    assert not isinstance(error.value, FenceTooLargeError)


def test_a_301m_side_is_too_large() -> None:
    with pytest.raises(FenceTooLargeError) as error:
        panel_fence_bom(sides_m=[Decimal(10), Decimal(301)], panel_height_cm=153)

    assert error.value.limit is FenceLimit.SIDE
    assert error.value.value_m == Decimal(301)
    assert error.value.max_m == MAX_SIDE_M


def test_a_side_just_over_300m_is_too_large() -> None:
    with pytest.raises(FenceTooLargeError) as error:
        panel_fence_bom(sides_m=[Decimal("300.01")], panel_height_cm=153)
    assert error.value.limit is FenceLimit.SIDE


def test_a_1200m_total_is_too_large() -> None:
    with pytest.raises(FenceTooLargeError) as error:
        panel_fence_bom(sides_m=[Decimal(300)] * 4, closed=True, panel_height_cm=153)

    assert error.value.limit is FenceLimit.TOTAL
    assert error.value.value_m == Decimal(1200)
    assert error.value.max_m == MAX_TOTAL_M


def test_a_total_just_over_1000m_is_too_large() -> None:
    sides = [Decimal(300), Decimal(300), Decimal(300), Decimal("100.01")]
    with pytest.raises(FenceTooLargeError) as error:
        panel_fence_bom(sides_m=sides, closed=True, panel_height_cm=153)

    assert error.value.limit is FenceLimit.TOTAL
    assert error.value.value_m == Decimal("1000.01")


@pytest.mark.parametrize(
    ("side", "expected_value_m"),
    [
        ("1e27", Decimal("1E+27")),
        ("123456789012345678901234567.89", Decimal("123456789012345678901234568")),
    ],
)
def test_a_side_too_big_for_decimal_precision_reports_a_rounded_length(
    side: str, expected_value_m: Decimal
) -> None:
    with pytest.raises(FenceTooLargeError) as error:
        panel_fence_bom(sides_m=[side], panel_height_cm=153)

    assert error.value.limit is FenceLimit.SIDE
    assert error.value.value_m == expected_value_m
    assert error.value.value_m == error.value.value_m.to_integral_value()


def test_too_large_is_still_a_value_error() -> None:
    # Callers that only catch ValueError still get no BOM instead of a crash.
    assert issubclass(FenceTooLargeError, ValueError)


def test_a_side_of_exactly_300m_gets_a_bom() -> None:
    lines = panel_fence_bom(sides_m=[Decimal(300)], panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == 120
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == 121


def test_exactly_1000m_in_total_gets_a_bom() -> None:
    sides = [Decimal(300), Decimal(300), Decimal(300), Decimal(100)]
    lines = panel_fence_bom(sides_m=sides, closed=True, panel_height_cm=153)

    assert line_for(lines, Item.PANEL).quantity == 400
    assert line_for(lines, Item.POST_BASE_PLATE).quantity == 400


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
        ({"panel_height_cm": 100}, "height"),
        ({"panel_height_cm": 153.0}, "height"),
        ({"panel_height_cm": "153"}, "height"),
        ({"panel_width_cm": 300}, "width"),
        ({"panel_width_cm": 250.0}, "width"),
    ],
)
def test_invalid_input_raises_value_error(kwargs: dict, message: str) -> None:
    arguments = {"sides_m": [Decimal(10)], "panel_height_cm": 153, **kwargs}
    with pytest.raises(ValueError, match=message):
        panel_fence_bom(**arguments)


# --- no braces on panel fences ----------------------------------------------------------


@pytest.mark.parametrize("panel_height_cm", sorted(PANEL_RULES))
@pytest.mark.parametrize(
    ("sides_m", "closed"),
    [
        ([Decimal(25)], False),  # straight: two free end posts
        ([Decimal(10), Decimal(10)], False),  # one corner plus two end posts
        ([Decimal(30), Decimal(10), Decimal(10)], False),  # a side past 25 m
        ([Decimal(10), Decimal(10), Decimal(10), Decimal(10)], True),  # closed loop
    ],
)
def test_panel_fence_never_gets_braces_or_support_posts(
    panel_height_cm: int, sides_m: list[Decimal], closed: bool
) -> None:
    # fence-rules.md, "Panel fence, any layout": no braces / support posts, end posts
    # included (Toni, 26 Nov 2026 14:29).
    lines = panel_fence_bom(sides_m=sides_m, panel_height_cm=panel_height_cm, closed=closed)
    assert Item.BRACE_T_POST not in quantities(lines)
    assert Item.POST_T not in quantities(lines)
    assert not any("potporn" in line.name.lower() for line in lines)


# --- anchors are "prateći materijal", always their own line (Toni, 28 Nov 2026, #171) ---


@pytest.mark.parametrize("height", sorted(PANEL_RULES))
@pytest.mark.parametrize(("sides", "closed"), [([3], False), ([10, 5], False), ([4] * 4, True)])
def test_every_panel_fence_lists_4_anchors_per_post(
    height: int, sides: list[int], closed: bool
) -> None:
    lines = panel_fence_bom(sides_m=sides, panel_height_cm=height, closed=closed)
    posts = line_for(lines, Item.POST_BASE_PLATE).quantity
    assert posts > 0
    assert line_for(lines, Item.ANCHOR_M8X80).quantity == 4 * posts
