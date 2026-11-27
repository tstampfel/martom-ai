"""Toni's review check for the BOM price map (#125): a mapped variant gone or unpriced."""

# ruff: noqa: RUF001

import dataclasses
from datetime import date
from decimal import Decimal

from bom_pricing_fixture import PANEL_LISTING, T_POST_LISTING, TENSIONER_LISTING, VARIANTS
from martom.bom.common import Item, PriceRef
from martom.bom.manual_products import ManualProduct, load_manual_products
from martom.bom.pricing import load_price_map
from martom.extract.store import VariantRow
from martom.review.checks import CheckListing, CheckProduct, check_bom_price_map, run_all_checks

MANUAL = load_manual_products()
PRICE_MAP = load_price_map()


def catalogue(overrides: dict[int, list[VariantRow]] | None = None) -> list[CheckListing]:
    """The mapped listings as active, extracted listings; `overrides` replaces variants."""
    data = {**VARIANTS, **(overrides or {})}
    return [
        CheckListing(
            id=listing_id,
            slug=f"oglas-{listing_id}",
            title=f"Oglas {listing_id}",
            description_text="",
            price=Decimal("1.00"),
            display_price="1 KM",
            price_by_agreement=False,
            state="new",
            attributes=(),
            photo_count=1,
            product=CheckProduct("ograda", None, variants),
        )
        for listing_id, variants in data.items()
    ]


def test_quiet_when_every_mapped_line_prices() -> None:
    assert check_bom_price_map(PRICE_MAP, catalogue(), MANUAL) == []


def test_unmapped_lines_raise_nothing() -> None:
    # The roll and the 120 cm T-post are known "na upit" lines, not problems.
    issues = check_bom_price_map(PRICE_MAP, catalogue(), MANUAL)
    assert all(issue.details["item"] != Item.ROLL_WELDED for issue in issues)


def test_variant_that_lost_its_price() -> None:
    variants = [
        dataclasses.replace(v, price=None) if v.height == "153" and v.length == "250 cm" else v
        for v in VARIANTS[PANEL_LISTING]
    ]
    issues = check_bom_price_map(PRICE_MAP, catalogue({PANEL_LISTING: variants}), MANUAL)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.kind == "bom_price_missing"
    assert issue.priority == "high"
    assert issue.listing_id == PANEL_LISTING
    assert issue.details == {
        "item": "panel",
        "size": "153 × 250 cm",
        "listing_id": PANEL_LISTING,
        "problem": "unpriced",
        "variants": [{"height_cm": 153, "length_cm": 250, "unit": "panel"}],
    }
    assert "„153 cm / 250 cm / panel“" in issue.message
    assert "cijena na upit" in issue.message


def test_only_the_failing_colour_is_named() -> None:
    # R1-9: Toni sees which tensioner colour to fix, not all three.
    variants = [v for v in VARIANTS[TENSIONER_LISTING] if v.colour != "Antracit"]
    issues = check_bom_price_map(PRICE_MAP, catalogue({TENSIONER_LISTING: variants}), MANUAL)
    assert len(issues) == 1
    assert issues[0].details["variants"] == [{"colour": "Antracit", "unit": "kom"}]
    assert "Antracit" in issues[0].message
    assert "Zeleni" not in issues[0].message


def test_manual_price_of_zero_is_reported() -> None:
    zero = {
        key: ManualProduct(p.key, p.name, Decimal("0.00"), p.source, p.last_confirmed)
        for key, p in MANUAL.items()
    }
    issues = check_bom_price_map(PRICE_MAP, catalogue(), zero)
    assert [(i.details["item"], i.details["problem"]) for i in issues] == [
        ("anchor_m8x70", "unpriced")
    ]
    # R2-2: its own text, never the OLX template with "oglas None".
    message = issues[0].message
    assert "proizvod „anchor_m8x70“ u manual_products.toml nema cijenu (0 KM)" in message
    assert "None" not in message
    assert "Provjerite oglas" not in message


def lost_panel_price() -> list[CheckListing]:
    variants = [
        dataclasses.replace(v, price=None) if v.height == "153" and v.length == "250 cm" else v
        for v in VARIANTS[PANEL_LISTING]
    ]
    return catalogue({PANEL_LISTING: variants})


def test_a_draft_map_issue_does_not_blame_the_variant_for_what_the_customer_sees() -> None:
    # The check still runs on a draft map (a pre-review check), but while the map is a
    # draft every line is "na upit" anyway, so the message says so instead.
    [issue] = check_bom_price_map(PRICE_MAP, lost_panel_price(), MANUAL)
    assert PRICE_MAP.status == "draft"
    assert "kupac vidi" not in issue.message
    assert "nacrt" in issue.message
    assert "nema cijenu" in issue.message


def test_a_reviewed_map_issue_says_the_customer_sees_na_upit() -> None:
    reviewed = dataclasses.replace(PRICE_MAP, status="reviewed")
    [issue] = check_bom_price_map(reviewed, lost_panel_price(), MANUAL)
    assert "kupac vidi „cijena na upit“" in issue.message
    assert "nacrt" not in issue.message


def test_variant_that_disappeared() -> None:
    variants = [v for v in VARIANTS[T_POST_LISTING] if v.height != "H-150"]
    issues = check_bom_price_map(PRICE_MAP, catalogue({T_POST_LISTING: variants}), MANUAL)
    assert [(i.details["item"], i.details["size"], i.details["problem"]) for i in issues] == [
        ("post_t", "150 cm", "variant_missing")
    ]


def test_inactive_listing_has_no_listing_id_so_the_report_still_shows_it() -> None:
    listings = [listing for listing in catalogue() if listing.id != T_POST_LISTING]
    issues = check_bom_price_map(PRICE_MAP, listings, MANUAL)
    # Every mapped T-post entry (125, 150, 175, 200 cm) is now "na upit".
    assert len(issues) == 4
    assert {issue.details["problem"] for issue in issues} == {"listing_inactive"}
    assert {issue.listing_id for issue in issues} == {None}


def test_the_fingerprint_survives_the_listing_going_inactive() -> None:
    variants = [v for v in VARIANTS[T_POST_LISTING] if v.height != "H-150"]
    missing = check_bom_price_map(PRICE_MAP, catalogue({T_POST_LISTING: variants}), MANUAL)
    inactive = check_bom_price_map(
        PRICE_MAP, [x for x in catalogue() if x.id != T_POST_LISTING], MANUAL
    )
    by_size = {i.details["size"]: i.fingerprint for i in inactive}
    assert missing[0].fingerprint == by_size["150 cm"]


def test_listing_without_product_row_is_inactive_for_pricing() -> None:
    listings = [
        dataclasses.replace(x, product=None) if x.id == T_POST_LISTING else x for x in catalogue()
    ]
    issues = check_bom_price_map(PRICE_MAP, listings, MANUAL)
    assert {issue.details["problem"] for issue in issues} == {"listing_inactive"}


def test_manual_entry_missing() -> None:
    issues = check_bom_price_map(PRICE_MAP, catalogue(), {})
    assert [(i.details["item"], i.details["problem"], i.listing_id) for i in issues] == [
        ("anchor_m8x70", "manual_missing", None)
    ]


def test_run_all_checks_includes_the_price_map_when_given() -> None:
    listings = [x for x in catalogue() if x.id != T_POST_LISTING]
    with_map = run_all_checks(listings, MANUAL, today=date(2026, 11, 27), price_map=PRICE_MAP)
    without = run_all_checks(listings, MANUAL, today=date(2026, 11, 27))
    assert sum(i.kind == "bom_price_missing" for i in with_map) == 4
    assert not any(i.kind == "bom_price_missing" for i in without)


def test_price_ref_of_a_sizeless_entry() -> None:
    assert PriceRef(Item.PVC_CLIP, None) in PRICE_MAP.entries
