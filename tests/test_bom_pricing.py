"""The BOM price map (#125): loading it, resolving a line's price, never guessing."""

# ruff: noqa: RUF001

import dataclasses
import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from bom_pricing_fixture import (
    GOLDEN_VARIANTS,
    PANEL_LISTING,
    T_POST_LISTING,
    TENSIONER_LISTING,
    VARIANTS,
    WIRE_LISTING,
)
from martom.bom.common import BomLine, Item, PriceRef
from martom.bom.manual_products import ManualProduct, load_manual_products
from martom.bom.panel import PANEL_RULES, PANEL_WIDTHS_CM, panel_fence_bom
from martom.bom.pricing import (
    PriceMap,
    PriceProblem,
    load_price_map,
    lookup_ref,
    matches_variant,
    resolve_price,
)
from martom.bom.roll import ROLL_RULES, RollPostModel, roll_fence_bom

MANUAL = load_manual_products()
SHIPPED = load_price_map()


def write_map(tmp_path: Path, body: str, status: str = "draft") -> Path:
    path = tmp_path / "price_map.toml"
    path.write_text(f'status = "{status}"\n{body}', encoding="utf-8")
    return path


def price_line(line: BomLine, price_map: PriceMap = SHIPPED, variants=VARIANTS) -> Decimal | None:
    """What the pricer does, with the fixture variants as the active catalogue."""
    ref = lookup_ref(line)
    if ref is None or ref not in price_map.entries:
        return None
    entry = price_map.entries[ref]
    found = variants.get(entry.listing_id) if entry.listing_id is not None else None
    return resolve_price(entry, found, MANUAL).price


# --- every line the calculators emit ----------------------------------------------------


def all_bom_lines() -> list[BomLine]:
    """Every line the panel and roll calculators can emit, over every height and option."""
    lines: list[BomLine] = []
    for height, width in itertools.product(PANEL_RULES, PANEL_WIDTHS_CM):
        lines += panel_fence_bom(sides_m=[10, 10], panel_height_cm=height, panel_width_cm=width)
    synced_options = (frozenset(), frozenset({PriceRef(Item.POST_T, "100 cm")}))
    for height, model, synced in itertools.product(ROLL_RULES, RollPostModel, synced_options):
        bom = roll_fence_bom(
            sides_m=[30, 10],
            roll_height_cm=height,
            post_model=model,
            synced_products=synced,
        )
        lines += bom.lines_with_wire()
    return lines


def test_every_emitted_line_is_mapped_unmapped_or_on_request() -> None:
    undecided = set()
    for line in all_bom_lines():
        ref = lookup_ref(line)
        if ref is not None and ref not in SHIPPED.entries and ref not in SHIPPED.unmapped:
            undecided.add(ref)
    assert undecided == set()


def test_every_item_the_calculators_have_appears_in_the_lines() -> None:
    # Guards the test above: a new Item the loops never reach would go unchecked.
    assert {line.item for line in all_bom_lines()} == set(Item)


def test_a_priced_line_has_exactly_its_mapped_variant_price() -> None:
    """No guess: each price is the price of the one variant the map names, or None."""
    for line in all_bom_lines():
        price = price_line(line)
        ref = lookup_ref(line)
        if ref is None or ref in SHIPPED.unmapped:
            assert price is None, line
            continue
        entry = SHIPPED.entries[ref]
        if entry.manual_key is not None:
            assert price == MANUAL[entry.manual_key].price_km
            continue
        variants = VARIANTS[entry.listing_id]
        matched = {
            v.price for match in entry.matches for v in variants if matches_variant(v, match)
        }
        assert matched == {price}, line


def test_the_905_10_km_example_prices_from_the_map() -> None:
    # fence-rules.md: 25 m straight, 153 cm panels, OLX prices of 26 Nov 2026.
    lines = panel_fence_bom(sides_m=[25], panel_height_cm=153)
    total = sum(line.quantity * price_line(line) for line in lines)
    assert total == Decimal("905.10")


def test_every_priced_line_of_a_roll_fence() -> None:
    bom = roll_fence_bom(sides_m=[30], roll_height_cm=125, post_model="foot_plate")
    prices = {line.item: price_line(line) for line in bom.lines_with_wire()}
    assert prices == {
        Item.ROLL_WELDED: None,  # no listing yet
        Item.POST_FOOT_PLATE: Decimal("16.50"),
        Item.BRACE_T_POST: Decimal("10.00"),  # T-post 125 cm via price_from
        Item.TENSIONER: Decimal("1.65"),
        Item.ANCHOR_M8X70: Decimal("0.80"),  # manual_products.toml
        Item.TENSION_WIRE: Decimal("18.00"),
        Item.WIRE_HOLDER: Decimal("0.40"),
    }


def test_t_post_120_cm_and_its_braces_are_on_request() -> None:
    bom = roll_fence_bom(sides_m=[30], roll_height_cm=80, post_model="t_post")
    prices = {line.item: price_line(line) for line in bom.lines}
    assert prices[Item.POST_T] is None
    assert prices[Item.BRACE_T_POST] is None


def test_t_post_100_cm_is_unmapped_until_the_brace_is_wired() -> None:
    # R1-8: no line can reach it yet (tools.py passes no synced_products).
    assert PriceRef(Item.POST_T, "100 cm") in SHIPPED.unmapped


def test_unconfirmed_100_cm_brace_stays_on_request() -> None:
    bom = roll_fence_bom(sides_m=[30], roll_height_cm=80, post_model="foot_plate")
    brace = next(line for line in bom.lines if line.item == Item.BRACE_T_POST)
    assert brace.unit_price_on_request
    assert lookup_ref(brace) is None
    assert price_line(brace) is None


# --- lookup_ref --------------------------------------------------------------------------


def test_lookup_ref_uses_price_from() -> None:
    line = BomLine(
        Item.BRACE_T_POST, "x", 2, size="125 cm", price_from=PriceRef(Item.POST_T, "125 cm")
    )
    assert lookup_ref(line) == PriceRef(Item.POST_T, "125 cm")


def test_lookup_ref_is_none_for_on_request_and_made_to_order() -> None:
    on_request = BomLine(Item.PANEL, "x", 1, size="153 × 250 cm", unit_price_on_request=True)
    assert lookup_ref(on_request) is None
    assert lookup_ref(BomLine(Item.PANEL, "x", 1, size="153 × 250 cm", made_to_order=True)) is None


def test_a_line_without_size_looks_up_by_item_alone() -> None:
    assert lookup_ref(BomLine(Item.PVC_CLIP, "x", 4)) == PriceRef(Item.PVC_CLIP, None)


# --- resolve_price: every way to end up "cijena na upit" --------------------------------


def panel_entry():
    return SHIPPED.entries[PriceRef(Item.PANEL, "153 × 250 cm")]


def test_inactive_or_unextracted_listing_is_on_request() -> None:
    result = resolve_price(panel_entry(), None, MANUAL)
    assert result.price is None
    assert result.problem == PriceProblem.LISTING_INACTIVE


def test_missing_variant_is_on_request() -> None:
    variants = [v for v in VARIANTS[PANEL_LISTING] if v.length != "250 cm"]
    result = resolve_price(panel_entry(), variants, MANUAL)
    assert (result.price, result.problem) == (None, PriceProblem.VARIANT_MISSING)


@pytest.mark.parametrize("height", ["H-125", "H-125 cm", "h - 125CM", "125 cm", "1250 mm"])
def test_a_height_written_another_way_still_matches(height: str) -> None:
    # Two extraction runs wrote the T-post height "H-125" and "H-125 cm" (R1-1): heights
    # and lengths compare as centimetres, so both price.
    rewritten = [
        dataclasses.replace(v, height=height) if "125" in (v.height or "") else v
        for v in VARIANTS[T_POST_LISTING]
    ]
    entry = SHIPPED.entries[PriceRef(Item.POST_T, "125 cm")]
    assert resolve_price(entry, rewritten, MANUAL).price == Decimal("10.00")


def test_a_height_that_is_not_one_number_never_matches() -> None:
    # "125 x 250 cm" is two sizes, not a height of 125 cm: no guess.
    rewritten = [
        dataclasses.replace(v, height="125 x 250 cm") if "125" in (v.height or "") else v
        for v in VARIANTS[T_POST_LISTING]
    ]
    entry = SHIPPED.entries[PriceRef(Item.POST_T, "125 cm")]
    assert resolve_price(entry, rewritten, MANUAL).problem == PriceProblem.VARIANT_MISSING


def test_text_fields_ignore_case_accents_and_spaces() -> None:
    entry = SHIPPED.entries[PriceRef(Item.TENSION_WIRE, "2,70 mm")]
    rewritten = [
        dataclasses.replace(v, size="2,70 mm", unit="100 m") if v.size == "2,70MM" else v
        for v in VARIANTS[WIRE_LISTING]
    ]
    assert resolve_price(entry, rewritten, MANUAL).price == Decimal("18.00")
    tensioner = SHIPPED.entries[PriceRef(Item.TENSIONER, None)]
    upper = [
        dataclasses.replace(v, colour=v.colour.upper().replace("Č", "C")) if v.colour else v
        for v in VARIANTS[TENSIONER_LISTING]
    ]
    assert resolve_price(tensioner, upper, MANUAL).price == Decimal("1.65")


def test_a_price_inside_the_label_does_not_break_the_clip_match() -> None:
    # The golden run wrote "PVC kopča sa samorezivim vijkom: 1,00 KM / kom" (R1-1).
    entry = SHIPPED.entries[PriceRef(Item.PVC_CLIP, None)]
    rewritten = [
        dataclasses.replace(v, label=f"{v.label}: 1,20 KM / kom", price=Decimal("1.20"))
        if v.label.startswith("PVC kopča")
        else v
        for v in VARIANTS[PANEL_LISTING]
    ]
    assert resolve_price(entry, rewritten, MANUAL).price == Decimal("1.20")


def test_shipped_map_prices_every_entry_against_the_golden_extraction_too() -> None:
    # Another extraction run of the same listings (tests/fixtures/extraction): the drift
    # between runs must not turn mapped lines "na upit".
    assert set(GOLDEN_VARIANTS) == {PANEL_LISTING, T_POST_LISTING}
    for ref, entry in SHIPPED.entries.items():
        if entry.listing_id in GOLDEN_VARIANTS:
            result = resolve_price(entry, GOLDEN_VARIANTS[entry.listing_id], MANUAL)
            assert result.problem is None, ref
            fixture = resolve_price(entry, VARIANTS[entry.listing_id], MANUAL)
            assert result.price == fixture.price, ref


def test_ambiguous_variant_is_on_request() -> None:
    target = next(
        v for v in VARIANTS[PANEL_LISTING] if v.label.startswith("3D Panelna ograda 153 x 250")
    )
    doubled = [*VARIANTS[PANEL_LISTING], dataclasses.replace(target, price=Decimal("50.00"))]
    result = resolve_price(panel_entry(), doubled, MANUAL)
    assert (result.price, result.problem) == (None, PriceProblem.VARIANT_AMBIGUOUS)


@pytest.mark.parametrize("price", [None, Decimal("0"), Decimal("-1")])
def test_unpriced_variant_is_on_request(price: Decimal | None) -> None:
    variants = [
        dataclasses.replace(v, price=price) if v.height == "153" and v.length == "250 cm" else v
        for v in VARIANTS[PANEL_LISTING]
    ]
    result = resolve_price(panel_entry(), variants, MANUAL)
    assert (result.price, result.problem) == (None, PriceProblem.UNPRICED)


def test_colours_with_different_prices_are_on_request() -> None:
    entry = SHIPPED.entries[PriceRef(Item.TENSIONER, None)]
    variants = [
        dataclasses.replace(v, price=Decimal("1.80")) if v.colour == "Antracit" else v
        for v in VARIANTS[TENSIONER_LISTING]
    ]
    result = resolve_price(entry, variants, MANUAL)
    assert (result.price, result.problem) == (None, PriceProblem.PRICES_DIFFER)
    assert result.failed_matches == entry.matches  # all colours: their prices disagree


def test_one_colour_missing_is_on_request() -> None:
    entry = SHIPPED.entries[PriceRef(Item.TENSIONER, None)]
    variants = [v for v in VARIANTS[TENSIONER_LISTING] if v.colour != "Cinčani"]
    result = resolve_price(entry, variants, MANUAL)
    assert result.problem == PriceProblem.VARIANT_MISSING
    # Only the missing colour is reported (R1-9).
    assert result.failed_matches == (entry.matches[2],)
    assert dict(entry.matches[2])["colour"] == "Cinčani"


def test_tensioner_colours_with_one_price_are_priced() -> None:
    entry = SHIPPED.entries[PriceRef(Item.TENSIONER, None)]
    result = resolve_price(entry, VARIANTS[TENSIONER_LISTING], MANUAL)
    assert (result.price, result.problem) == (Decimal("1.65"), None)


@pytest.mark.parametrize("price", ["0", "0.00"])
def test_manual_price_of_zero_is_on_request(price: str) -> None:
    # R1-3: 0 KM never counts as a price, for manual products as for OLX variants.
    entry = SHIPPED.entries[PriceRef(Item.ANCHOR_M8X70, "M8×70")]
    zero = {
        key: ManualProduct(p.key, p.name, Decimal(price), p.source, p.last_confirmed)
        for key, p in MANUAL.items()
    }
    result = resolve_price(entry, None, zero)
    assert (result.price, result.problem) == (None, PriceProblem.UNPRICED)


def test_manual_entry_missing_from_manual_products_is_on_request() -> None:
    entry = SHIPPED.entries[PriceRef(Item.ANCHOR_M8X70, "M8×70")]
    result = resolve_price(entry, None, {})
    assert (result.price, result.problem) == (None, PriceProblem.MANUAL_MISSING)


# --- the shipped file ---------------------------------------------------------------------


def test_shipped_map_is_a_draft_until_reviewed() -> None:
    assert SHIPPED.status == "draft"


def test_shipped_manual_entries_exist_in_manual_products() -> None:
    for entry in SHIPPED.entries.values():
        if entry.manual_key is not None:
            assert entry.manual_key in MANUAL


def test_shipped_listing_entries_resolve_against_the_synced_variants() -> None:
    # Drafted from the catalogue: today every entry prices (the review check stays quiet).
    for ref, entry in SHIPPED.entries.items():
        found = VARIANTS.get(entry.listing_id) if entry.listing_id is not None else None
        result = resolve_price(entry, found, MANUAL)
        assert result.problem is None, ref


# --- load_price_map validation -------------------------------------------------------------


GOOD_ENTRY = """
[[entry]]
item = "panel"
size = "153 × 250 cm"
listing_id = 63996799
variants = [{ height_cm = 153, length_cm = 250, unit = "panel" }]
"""


def test_loads_a_minimal_map(tmp_path: Path) -> None:
    price_map = load_price_map(write_map(tmp_path, GOOD_ENTRY, status="reviewed"))
    assert price_map.status == "reviewed"
    entry = price_map.entries[PriceRef(Item.PANEL, "153 × 250 cm")]
    assert entry.listing_id == PANEL_LISTING
    assert entry.matches == ((("height_cm", 153), ("length_cm", 250), ("unit", "panel")),)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (GOOD_ENTRY.replace('"panel"', '"pannel"'), "not a BOM item"),
        (GOOD_ENTRY.replace("listing_id = 63996799\n", ""), "exactly one of listing_id"),
        (GOOD_ENTRY + 'manual = "anchor_m8x70"\n', "exactly one of listing_id"),
        (GOOD_ENTRY.replace("63996799", '"63996799"'), "listing_id"),
        (GOOD_ENTRY.replace("63996799", "true"), "listing_id"),
        (
            GOOD_ENTRY.replace('{ height_cm = 153, length_cm = 250, unit = "panel" }', ""),
            "variants",
        ),
        (GOOD_ENTRY.replace('height_cm = 153, length_cm = 250, unit = "panel"', ""), "unit"),
        (GOOD_ENTRY.replace(', unit = "panel"', ""), "unit"),
        (GOOD_ENTRY.replace("height_cm = 153", 'price = "53"'), "field 'price'"),
        (GOOD_ENTRY.replace("height_cm = 153", 'label = "Panel"'), "field 'label'"),
        (GOOD_ENTRY.replace("height_cm = 153", 'height = "153"'), "field 'height'"),
        (GOOD_ENTRY.replace("height_cm = 153", 'height_cm = "153"'), "positive whole number"),
        (GOOD_ENTRY.replace("height_cm = 153", "height_cm = 0"), "positive whole number"),
        (GOOD_ENTRY.replace("height_cm = 153", "height_cm = true"), "positive whole number"),
        (GOOD_ENTRY.replace('unit = "panel"', "unit = 1"), "non-empty string"),
        (GOOD_ENTRY.replace('unit = "panel"', 'unit = " "'), "non-empty string"),
        (GOOD_ENTRY + GOOD_ENTRY, "twice"),
        (GOOD_ENTRY + 'colour = "zelena"\n', "unknown key"),
        (
            GOOD_ENTRY + '[[unmapped]]\nitem = "panel"\nsize = "153 × 250 cm"\nreason = "x"\n',
            "twice",
        ),
        ('[[unmapped]]\nitem = "panel"\n', "reason"),
    ],
)
def test_rejects_a_bad_entry(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_price_map(write_map(tmp_path, body))


@pytest.mark.parametrize("status", ["", "approved"])
def test_rejects_a_bad_status(tmp_path: Path, status: str) -> None:
    with pytest.raises(ValueError, match="status"):
        load_price_map(write_map(tmp_path, GOOD_ENTRY, status=status))


def test_manual_entry_takes_no_variants(tmp_path: Path) -> None:
    body = (
        '[[entry]]\nitem = "anchor_m8x70"\nmanual = "anchor_m8x70"\nvariants = [{ unit = "kom" }]\n'
    )
    with pytest.raises(ValueError, match="variants"):
        load_price_map(write_map(tmp_path, body))
