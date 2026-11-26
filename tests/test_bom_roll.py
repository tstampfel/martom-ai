"""Welded roll fence BOM: quantities follow docs/domain/fence-rules.md (Toni, 26 Nov 2026).

Tests named `..._pending_toni` use a rule fence-rules.md marks as an assumption still
waiting for Toni's confirmation; change them when he answers.

Tension wire is a suggested add-on, not a BOM line (fence-rules.md, "Accessories"):
the result carries it in `wire_addon`, outside `lines` and outside the BOM total.
"""

# The labels under test use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

from decimal import Decimal

import pytest

from martom.bom.common import (
    MAX_SIDE_M,
    MAX_TOTAL_M,
    BomLine,
    FenceLimit,
    FenceTooLargeError,
    Item,
    PriceRef,
)
from martom.bom.manual_products import load_manual_products
from martom.bom.roll import (
    ROLL_RULES,
    SPACING_NOTE,
    T_POST_HEIGHTS_CM,
    RollFenceBom,
    RollMesh,
    RollPostModel,
    RulesPendingError,
    roll_fence_bom,
)

T_POST = RollPostModel.T_POST
FOOT_PLATE = RollPostModel.FOOT_PLATE


def quantities(result: RollFenceBom) -> dict[Item, int]:
    return {line.item: line.quantity for line in result.lines}


def line_for(result: RollFenceBom, item: Item) -> BomLine:
    matches = [line for line in result.lines if line.item == item]
    assert len(matches) == 1, f"expected exactly one {item} line, got {matches}"
    return matches[0]


def bom(
    sides_m: list[int | str],
    *,
    roll_height_cm: int = 100,
    post_model: RollPostModel = T_POST,
    closed: bool = False,
) -> RollFenceBom:
    return roll_fence_bom(
        sides_m=sides_m,
        roll_height_cm=roll_height_cm,
        post_model=post_model,
        closed=closed,
    )


def addon_quantities(result: RollFenceBom) -> dict[Item, int]:
    return {line.item: line.quantity for line in result.wire_addon}


# --- rules table ------------------------------------------------------------------------


def test_t_post_is_the_smallest_available_height_at_least_roll_plus_30_cm() -> None:
    # Toni's rule (fence-rules.md, "T-post (a) height"); 80 -> 120 is confirmed.
    for roll_height, rule in ROLL_RULES.items():
        expected = min(h for h in T_POST_HEIGHTS_CM if h >= roll_height + 30)
        assert rule.t_post_height_cm == expected, roll_height


def test_rules_table_covers_the_roll_heights_in_fence_rules() -> None:
    assert sorted(ROLL_RULES) == [60, 80, 100, 125, 150]
    assert T_POST_HEIGHTS_CM == (120, 125, 150, 175, 200)


def test_wire_rows_are_2_up_to_100_cm_and_3_above() -> None:
    assert {h: rule.wire_rows for h, rule in ROLL_RULES.items()} == {
        60: 2,
        80: 2,
        100: 2,
        125: 3,
        150: 3,
    }


# --- posts ------------------------------------------------------------------------------


def test_25m_straight_has_14_posts() -> None:
    assert quantities(bom([25]))[Item.POST_T] == 14


def test_10m_straight_has_6_posts() -> None:
    assert quantities(bom([10]))[Item.POST_T] == 6


def test_two_10m_sides_with_one_corner_share_the_corner_post() -> None:
    assert quantities(bom([10, 10]))[Item.POST_T] == 11


def test_30m_straight_has_16_posts_because_the_strainer_replaces_a_line_post() -> None:
    assert quantities(bom([30]))[Item.POST_T] == 16


def test_closed_loop_has_as_many_posts_as_the_sides_minus_shared_corners() -> None:
    # 4 × 10 m around a yard: 4 × 6 posts - 4 corners.
    assert quantities(bom([10, 10, 10, 10], closed=True))[Item.POST_T] == 20


@pytest.mark.parametrize(
    ("side_m", "posts"),
    [
        ("10.5", 7),  # ceil(5.25) + 1
        ("1", 2),  # a short run still needs both ends
        ("2.01", 3),
        ("25.01", 14),  # ceil(12.505) + 1
        ("50", 26),
    ],
)
def test_odd_lengths_round_the_post_count_up(side_m: str, posts: int) -> None:
    assert quantities(bom([side_m]))[Item.POST_T] == posts


def test_float_noise_does_not_add_a_post() -> None:
    assert quantities(bom([0.1 * 3 * 20]))[Item.POST_T] == 4  # 6.000000000000001 m


def test_80cm_roll_uses_120cm_t_posts() -> None:
    post = line_for(bom([10], roll_height_cm=80), Item.POST_T)
    assert post.size == "120 cm"
    assert post.name == "T-stup 120 cm"


def test_100cm_roll_uses_150cm_t_posts() -> None:
    assert line_for(bom([10], roll_height_cm=100), Item.POST_T).size == "150 cm"


def test_foot_plate_post_is_the_fence_height() -> None:
    post = line_for(bom([10], roll_height_cm=100, post_model=FOOT_PLATE), Item.POST_FOOT_PLATE)
    assert post.size == "100 cm"
    assert post.name == "Stup sa stopom 100 cm"


def test_a_fence_uses_one_post_model_only() -> None:
    assert Item.POST_FOOT_PLATE not in quantities(bom([10]))
    assert Item.POST_T not in quantities(bom([10], post_model=FOOT_PLATE))


# --- rolls ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sides_m", "rolls"),
    [([25], 1), ([30], 2), ([10, 10], 1), ([24, 2], 2), (["0.5"], 1), ([50], 2)],
)
def test_rolls_are_the_total_length_over_25m_rounded_up(sides_m: list[int], rolls: int) -> None:
    assert quantities(bom(sides_m))[Item.ROLL_WELDED] == rolls


def test_roll_line_names_its_height() -> None:
    roll = line_for(bom([25], roll_height_cm=125), Item.ROLL_WELDED)
    assert roll.size == "125 cm"
    assert roll.name == "Varena ograda u roli 125 cm (25 m)"


# --- strainers and braces ---------------------------------------------------------------


def test_straight_25m_fence_has_no_braces() -> None:
    # No corner and no strainer; the free end posts get none until Toni answers.
    assert Item.BRACE_T_POST not in quantities(bom([25]))


def test_30m_straight_gets_2_support_t_posts_for_its_one_strainer() -> None:
    assert quantities(bom([30]))[Item.BRACE_T_POST] == 2


def test_corner_post_gets_2_support_t_posts_and_the_ends_none() -> None:
    assert quantities(bom([10, 10]))[Item.BRACE_T_POST] == 2


def test_strainers_and_corners_add_up_for_the_braces() -> None:
    # 30 m side: 1 strainer; 10 m side: none; 1 corner -> 2 × (1 + 1).
    assert quantities(bom([30, 10]))[Item.BRACE_T_POST] == 4


def test_closed_loop_braces_every_corner() -> None:
    assert quantities(bom([10, 10, 10, 10], closed=True))[Item.BRACE_T_POST] == 8


@pytest.mark.parametrize(("side_m", "braces"), [(25, 0), ("25.01", 2), (50, 2), ("50.01", 4)])
def test_a_strainer_stands_at_every_25m_inside_a_side(side_m: int | str, braces: int) -> None:
    assert quantities(bom([side_m])).get(Item.BRACE_T_POST, 0) == braces


def test_t_post_brace_is_the_t_post_height_priced_as_that_t_post() -> None:
    brace = line_for(bom([30], roll_height_cm=80), Item.BRACE_T_POST)
    assert brace.size == "120 cm"
    assert brace.name == "Potporni T-stup 120 cm"
    assert brace.price_from == PriceRef(item=Item.POST_T, size="120 cm")
    assert not brace.unit_price_on_request


def test_foot_plate_fence_125cm_with_a_corner_gets_two_125cm_t_posts_as_braces() -> None:
    brace = line_for(bom([10, 10], roll_height_cm=125, post_model=FOOT_PLATE), Item.BRACE_T_POST)
    assert brace.quantity == 2
    assert brace.size == "125 cm"
    assert brace.price_from == PriceRef(item=Item.POST_T, size="125 cm")


# The synced price table as the caller holds it: PriceRef -> unit price. The prices are
# made up; the BOM only checks which products are there and never copies a price.
T_POST_100_CM = PriceRef(item=Item.POST_T, size="100 cm")
SYNCED_WITHOUT_100_CM = {PriceRef(item=Item.POST_T, size="120 cm"): Decimal("9.99")}
SYNCED_WITH_100_CM = {**SYNCED_WITHOUT_100_CM, T_POST_100_CM: Decimal("7.77")}


def short_foot_plate_brace(
    roll_height_cm: int, synced_prices: dict[PriceRef, Decimal] | None = None
) -> BomLine:
    kwargs = {} if synced_prices is None else {"synced_prices": synced_prices}
    result = roll_fence_bom(
        sides_m=[10, 10], roll_height_cm=roll_height_cm, post_model=FOOT_PLATE, **kwargs
    )
    return line_for(result, Item.BRACE_T_POST)


@pytest.mark.parametrize("roll_height_cm", [60, 80, 100])
@pytest.mark.parametrize("synced_prices", [None, {}, SYNCED_WITHOUT_100_CM])
def test_short_foot_plate_brace_is_100cm_on_request_until_the_variant_is_synced(
    roll_height_cm: int, synced_prices: dict[PriceRef, Decimal] | None
) -> None:
    # Foot-plate fence 80 cm, 1 corner -> "potporni stup 100 cm – cijena na upit",
    # 2 pieces, size 100 cm, no price (Toni 26 Nov 14:29; the variant isn't on OLX yet).
    brace = short_foot_plate_brace(roll_height_cm, synced_prices)
    assert brace.quantity == 2
    assert brace.size == "100 cm"
    assert brace.name == "Potporni stup 100 cm"
    assert brace.label == "potporni stup 100 cm – cijena na upit"
    assert brace.unit_price_on_request
    assert brace.price_from is None


@pytest.mark.parametrize("roll_height_cm", [60, 80, 100])
def test_short_foot_plate_brace_takes_the_synced_100cm_t_post_price(roll_height_cm: int) -> None:
    brace = short_foot_plate_brace(roll_height_cm, SYNCED_WITH_100_CM)
    assert brace.quantity == 2
    assert brace.size == "100 cm"
    assert brace.name == "Potporni T-stup 100 cm"
    assert brace.label is None
    assert not brace.unit_price_on_request
    # The price is looked up from the 100 cm variant of T-post 59473597, never stored here.
    assert brace.price_from == T_POST_100_CM


def test_short_foot_plate_brace_count_follows_corners_and_strainers() -> None:
    result = roll_fence_bom(
        sides_m=[30, 10, 10, 10], roll_height_cm=80, post_model=FOOT_PLATE, closed=True
    )
    # 4 corners + 1 strainer (inside the 30 m side) -> 2 × 5 = 10.
    assert line_for(result, Item.BRACE_T_POST).quantity == 10


def test_synced_prices_dont_change_tall_fence_braces() -> None:
    result = roll_fence_bom(
        sides_m=[10, 10],
        roll_height_cm=125,
        post_model=FOOT_PLATE,
        synced_prices=SYNCED_WITH_100_CM,
    )
    brace = line_for(result, Item.BRACE_T_POST)
    assert brace.price_from == PriceRef(item=Item.POST_T, size="125 cm")


def test_t_post_fence_never_gets_a_100cm_brace() -> None:
    result = roll_fence_bom(
        sides_m=[10, 10], roll_height_cm=80, post_model=T_POST, synced_prices=SYNCED_WITH_100_CM
    )
    assert line_for(result, Item.BRACE_T_POST).size == "120 cm"


def test_no_line_quotes_a_price_from_the_synced_table() -> None:
    # Grounding: BOM lines hold quantities and price references, never a unit price.
    result = roll_fence_bom(
        sides_m=[10, 10], roll_height_cm=80, post_model=FOOT_PLATE, synced_prices=SYNCED_WITH_100_CM
    )
    for line in (*result.lines, *result.wire_addon):
        assert "7.77" not in repr(line)
        assert "7,77" not in repr(line)


# --- tensioners -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sides_m", "tensioners"),
    [([25], 3), ([30], 6), ([10, 10], 6), ([10], 3), ([50], 6), (["50.01"], 9)],
)
def test_tensioners_are_3_per_started_25m_on_each_side(
    sides_m: list[int | str], tensioners: int
) -> None:
    assert quantities(bom(sides_m))[Item.TENSIONER] == tensioners


# --- tension wire add-on and wire holders ----------------------------------------------


def test_tension_wire_is_never_a_bom_line() -> None:
    for model in (T_POST, FOOT_PLATE):
        lines = quantities(bom([25], roll_height_cm=125, post_model=model))
        assert Item.TENSION_WIRE not in lines
        assert Item.WIRE_HOLDER not in lines


@pytest.mark.parametrize(
    ("roll_height_cm", "sides_m", "coils"),
    [
        (125, [25], 1),  # 3 rows × 25 m = 75 m
        (150, [40], 2),  # 3 rows × 40 m = 120 m
        (80, [25], 1),  # 2 rows × 25 m = 50 m
        (100, [50], 1),  # 2 rows × 50 m = exactly one 100 m coil
        (100, ["50.01"], 2),
        (125, [10, 10, 10, 10], 2),  # closed loop: 3 rows × 40 m
    ],
)
def test_wire_addon_coils_are_rows_times_total_over_100m(
    roll_height_cm: int, sides_m: list[int | str], coils: int
) -> None:
    result = bom(sides_m, roll_height_cm=roll_height_cm, closed=len(sides_m) > 2)
    [wire] = [line for line in result.wire_addon if line.item == Item.TENSION_WIRE]
    assert wire.quantity == coils
    assert wire.size == "2,70 mm"
    assert wire.name == "Nosiva žica 2,70 mm (kolut 100 m)"


def test_t_post_fence_wire_addon_has_no_wire_holders() -> None:
    assert addon_quantities(bom([25], roll_height_cm=125)) == {Item.TENSION_WIRE: 1}


def test_foot_plate_fence_10_posts_125cm_wire_addon_has_30_wire_holders_pending_toni() -> None:
    # 18 m straight = 10 posts; 3 rows × 10 posts (1 per row per post is an assumption).
    result = bom([18], roll_height_cm=125, post_model=FOOT_PLATE)
    assert quantities(result)[Item.POST_FOOT_PLATE] == 10
    assert addon_quantities(result) == {Item.TENSION_WIRE: 1, Item.WIRE_HOLDER: 30}


def test_foot_plate_fence_100cm_wire_addon_has_2_holders_per_post_pending_toni() -> None:
    assert addon_quantities(bom([18], roll_height_cm=100, post_model=FOOT_PLATE)) == {
        Item.TENSION_WIRE: 1,  # 2 rows × 18 m = 36 m
        Item.WIRE_HOLDER: 20,
    }


def test_lines_with_wire_appends_the_addon_as_normal_lines() -> None:
    result = bom([18], roll_height_cm=125, post_model=FOOT_PLATE)
    assert result.lines_with_wire() == result.lines + result.wire_addon
    # The add-on lines are ordinary BomLines, removable like any accessory.
    assert all(isinstance(line, BomLine) for line in result.lines_with_wire())


# --- anchors ----------------------------------------------------------------------------


def test_foot_plate_fence_with_10_posts_needs_30_m8x70_anchors_at_0_80_km() -> None:
    lines = bom([18], post_model=FOOT_PLATE)
    anchor = line_for(lines, Item.ANCHOR_M8X70)
    assert anchor.quantity == 30
    assert anchor.size == "M8×70"
    assert anchor.name == "Anker M8×70"
    # Price and name come from the reviewed manual_products config, keyed by the item value;
    # the line's label must not drift from it.
    entry = load_manual_products()[anchor.item.value]
    assert entry.price_km == Decimal("0.80")
    assert anchor.name == entry.name


def test_t_post_fence_has_no_anchors() -> None:
    lines = quantities(bom([25]))
    assert Item.ANCHOR_M8X70 not in lines
    assert Item.ANCHOR_M8X80 not in lines


# --- the full list ----------------------------------------------------------------------


def test_full_t_post_list_for_30m_straight_125cm() -> None:
    result = bom([30], roll_height_cm=125)
    assert quantities(result) == {
        Item.ROLL_WELDED: 2,
        Item.POST_T: 16,
        Item.BRACE_T_POST: 2,
        Item.TENSIONER: 6,
    }
    assert addon_quantities(result) == {Item.TENSION_WIRE: 1}  # 3 rows × 30 m = 90 m


def test_full_foot_plate_list_for_a_closed_loop() -> None:
    result = bom([10, 10, 10, 10], roll_height_cm=150, post_model=FOOT_PLATE, closed=True)
    assert quantities(result) == {
        Item.ROLL_WELDED: 2,
        Item.POST_FOOT_PLATE: 20,
        Item.BRACE_T_POST: 8,
        Item.TENSIONER: 12,
        Item.ANCHOR_M8X70: 60,
    }
    assert addon_quantities(result) == {
        Item.TENSION_WIRE: 2,  # 3 rows × 40 m = 120 m
        Item.WIRE_HOLDER: 60,
    }


def test_every_line_has_a_positive_quantity() -> None:
    for sides in ([1], [25], [30, 10], [300, 300, 300]):
        for line in bom(sides, post_model=FOOT_PLATE).lines_with_wire():
            assert line.quantity > 0, line


def test_no_line_is_made_to_order() -> None:
    lines = bom([30, 10], roll_height_cm=60, post_model=FOOT_PLATE).lines_with_wire()
    assert not any(line.made_to_order for line in lines)


def test_no_gate_line() -> None:
    assert all("kapij" not in line.name.lower() for line in bom([30, 10]).lines)


# --- spacing ----------------------------------------------------------------------------


def test_3m_spacing_request_still_gives_the_2m_bom_with_a_note() -> None:
    plain = bom([25])
    wider = roll_fence_bom(
        sides_m=[25], roll_height_cm=100, post_model=T_POST, requested_spacing_m=3
    )
    assert wider.lines == plain.lines
    assert wider.spacing_note == SPACING_NOTE
    assert SPACING_NOTE == "razmak do 3 m ovisno o terenu – potvrdite s Martomom"


def test_no_spacing_request_has_no_note() -> None:
    assert bom([25]).spacing_note is None


@pytest.mark.parametrize("spacing", [2, "2.0", Decimal("1.5")])
def test_spacing_request_of_2m_or_less_has_no_note(spacing: int | str | Decimal) -> None:
    result = roll_fence_bom(
        sides_m=[25], roll_height_cm=100, post_model=T_POST, requested_spacing_m=spacing
    )
    assert result.spacing_note is None


@pytest.mark.parametrize("spacing", ["9e999999", "1e9999999999"])
def test_huge_spacing_request_still_gives_the_2m_bom_with_a_note(spacing: str) -> None:
    # Must not crash with decimal.Overflow on the metres -> centimetres scaling.
    result = roll_fence_bom(
        sides_m=[25], roll_height_cm=100, post_model=T_POST, requested_spacing_m=spacing
    )
    assert result.lines == bom([25]).lines
    assert result.spacing_note == SPACING_NOTE


@pytest.mark.parametrize("spacing", [0, -1, "abc", float("nan"), True])
def test_invalid_spacing_request_is_an_input_error(spacing: object) -> None:
    with pytest.raises(ValueError, match="spacing"):
        roll_fence_bom(
            sides_m=[25],
            roll_height_cm=100,
            post_model=T_POST,
            requested_spacing_m=spacing,  # type: ignore[arg-type]
        )


# --- chain link and input errors --------------------------------------------------------


@pytest.mark.parametrize("mesh", [RollMesh.CHAIN_LINK, "chain_link"])
def test_chain_link_raises_rules_pending(mesh: RollMesh | str) -> None:
    with pytest.raises(RulesPendingError, match="chain link"):
        roll_fence_bom(sides_m=[25], roll_height_cm=100, post_model=T_POST, mesh=mesh)


def test_rules_pending_is_a_value_error() -> None:
    assert issubclass(RulesPendingError, ValueError)


def test_welded_is_the_default_mesh() -> None:
    explicit = roll_fence_bom(
        sides_m=[25], roll_height_cm=100, post_model=T_POST, mesh=RollMesh.WELDED
    )
    assert explicit == bom([25])


def test_unknown_mesh_is_an_input_error() -> None:
    with pytest.raises(ValueError, match="mesh"):
        roll_fence_bom(sides_m=[25], roll_height_cm=100, post_model=T_POST, mesh="eurofence")


def test_post_model_accepts_its_string_value() -> None:
    assert roll_fence_bom(sides_m=[10], roll_height_cm=100, post_model="foot_plate") == bom(
        [10], post_model=FOOT_PLATE
    )


def test_unknown_post_model_is_an_input_error() -> None:
    with pytest.raises(ValueError, match="post model"):
        roll_fence_bom(sides_m=[10], roll_height_cm=100, post_model="wooden")


@pytest.mark.parametrize("height", [90, 0, 120, "100", 100.0, True])
def test_unknown_roll_height_is_an_input_error(height: object) -> None:
    with pytest.raises(ValueError, match="roll height"):
        roll_fence_bom(
            sides_m=[10],
            roll_height_cm=height,  # type: ignore[arg-type]
            post_model=T_POST,
        )


@pytest.mark.parametrize("sides", [[], [0], [-5], ["abc"], [None], "25", [float("inf")]])
def test_invalid_sides_are_input_errors(sides: object) -> None:
    with pytest.raises(ValueError):
        roll_fence_bom(sides_m=sides, roll_height_cm=100, post_model=T_POST)  # type: ignore[arg-type]


def test_closed_fence_needs_three_sides() -> None:
    with pytest.raises(ValueError, match="at least 3 sides"):
        bom([10, 10], closed=True)


def test_side_over_300m_is_too_large() -> None:
    with pytest.raises(FenceTooLargeError) as error:
        bom([MAX_SIDE_M + 1])
    assert error.value.limit == FenceLimit.SIDE


def test_total_over_1000m_is_too_large() -> None:
    with pytest.raises(FenceTooLargeError) as error:
        bom([300, 300, 300, 101])
    assert error.value.limit == FenceLimit.TOTAL
    assert error.value.max_m == MAX_TOTAL_M


def test_exactly_the_limits_is_allowed() -> None:
    lines = quantities(bom([300, 300, 300, 100]))
    assert lines[Item.ROLL_WELDED] == 40
