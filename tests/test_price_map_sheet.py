"""Toni's review sheet for the BOM price map (#125): rows and Markdown, no database."""

# ruff: noqa: RUF001

import dataclasses
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from bom_pricing_fixture import PANEL_LISTING, TENSIONER_LISTING, VARIANTS
from martom.bom.manual_products import load_manual_products
from martom.bom.pricing import load_price_map
from martom.extract.store import VariantRow, save_products
from martom.review.price_map_sheet import (
    HEADER,
    SheetListing,
    SheetRow,
    build_rows,
    load_listings,
    load_roll_candidates,
    render_markdown,
)
from search_catalogue import INACTIVE_PANEL, add_listing, build_catalogue, synthetic

MANUAL = load_manual_products()
PRICE_MAP = load_price_map()
LISTINGS = {
    listing_id: SheetListing(listing_id, f"Oglas {listing_id}", f"oglas-{listing_id}", variants)
    for listing_id, variants in VARIANTS.items()
}
TODAY = date(2026, 11, 27)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def roll_candidate(unit: str = "m") -> SheetListing:
    variant = VariantRow("61 cm -6,00KM/m", "61 cm", None, "antracit", None, "6,00KM/m",
                         Decimal("6.00"), unit)  # fmt: skip
    return SheetListing(59713743, "Ograde u roli antracit", "ograde-u-roli-59713743", [variant])


def rows_for(item: str, rows: list[SheetRow]) -> list[SheetRow]:
    return [row for row in rows if row.item == item]


# --- render_markdown --------------------------------------------------------------------


def test_renders_one_fake_row_with_empty_columns_for_toni() -> None:
    row = SheetRow(
        item="Panel",
        key="153 × 250 cm",
        listing="PANEL | 3D\nOGRADA",
        variant="3D Panelna ograda 153 x 250 cm",
        price="53,00 KM",
        link="https://martom.olx.ba/artikal/panel-63996799",
        note="",
    )
    text = render_markdown([row], today=TODAY, status="draft")
    lines = text.splitlines()
    assert lines[0] == "# Cijene za popis materijala ograde – provjera za Tonija"
    header = "| " + " | ".join(HEADER) + " |"
    assert header in lines
    assert HEADER[-2:] == ("OK", "Pogrešno / napomena")
    assert (
        "| Panel | 153 × 250 cm | PANEL \\| 3D OGRADA | 3D Panelna ograda 153 x 250 cm "
        "| 53,00 KM | https://martom.olx.ba/artikal/panel-63996799 |  |  |"
    ) in lines
    assert "27.11.2026" in text
    assert "nacrt" in text
    assert "nije sužen na varenu mrežu" in text  # roll candidates include PVC fences


# --- build_rows -------------------------------------------------------------------------


def test_a_mapped_panel_row_has_the_variant_as_written_the_price_and_the_link() -> None:
    rows = build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[])
    panel = next(r for r in rows_for("Panel", rows) if r.key == "153 × 250 cm")
    assert panel.listing == f"Oglas {PANEL_LISTING}"
    assert panel.variant == "3D Panelna ograda 153 x 250 cm (Ø 4,20/4,40 mm PVC)"
    assert panel.price == "53,00 KM"
    assert panel.link == f"https://martom.olx.ba/artikal/oglas-{PANEL_LISTING}"
    assert panel.note == ""


def test_the_tensioner_gets_one_row_per_colour() -> None:
    rows = rows_for("Zatezač (španer)", build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[]))
    assert [r.key for r in rows] == ["boja: Zeleni", "boja: Antracit", "boja: Cinčani"]
    assert {r.price for r in rows} == {"1,65 KM"}
    assert all(str(TENSIONER_LISTING) in (r.link or "") for r in rows)


def test_manual_product_row() -> None:
    rows = rows_for(
        "Anker vijak M8×70", build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[])
    )
    assert len(rows) == 1
    assert rows[0].listing == "bez OLX oglasa (manual_products.toml)"
    assert rows[0].price == "0,80 KM"
    assert rows[0].link == ""


def test_unmapped_item_says_why() -> None:
    rows = rows_for("T-stup", build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[]))
    t_120 = next(r for r in rows if r.key == "120 cm")
    assert t_120.listing == "nije povezano"
    assert "nema varijantu 120 cm" in t_120.note
    assert t_120.price == ""


def test_inactive_listing_and_missing_variant_are_noted() -> None:
    listings = {k: v for k, v in LISTINGS.items() if k != TENSIONER_LISTING}
    rows = rows_for("Zatezač (španer)", build_rows(PRICE_MAP, listings, MANUAL, roll_candidates=[]))
    assert {r.note for r in rows} == {
        "oglas nije aktivan ili nije obrađen → kupac vidi „cijena na upit“"
    }
    assert {r.price for r in rows} == {""}


def test_roll_rows_show_the_per_metre_candidate_and_the_25_m_price_to_confirm() -> None:
    rows = rows_for(
        "Varena ograda u roli",
        build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[roll_candidate()]),
    )
    sixty = [r for r in rows if r.key == "60 cm"]
    assert len(sixty) == 1
    assert sixty[0].listing == "Ograde u roli antracit"
    assert sixty[0].variant == "61 cm -6,00KM/m"
    assert sixty[0].price == "150,00 KM za 25 m (25 × 6,00 KM/m) – za potvrdu"
    assert sixty[0].link == "https://martom.olx.ba/artikal/ograde-u-roli-59713743"
    assert "nije povezano" in sixty[0].note
    # No candidate within 5 cm of 80 cm: the plain "nije povezano" row.
    eighty = [r for r in rows if r.key == "80 cm"]
    assert [(r.listing, r.price) for r in eighty] == [("nije povezano", "")]


def test_roll_candidate_priced_per_piece_is_not_offered() -> None:
    rows = rows_for(
        "Varena ograda u roli",
        build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[roll_candidate(unit="kom")]),
    )
    assert all(r.listing == "nije povezano" for r in rows)


def test_the_note_follows_the_pricing_rule_when_colours_cost_differently() -> None:
    # The customer sees "na upit" when the tensioner colours differ: so must the sheet.
    variants = [
        dataclasses.replace(v, price=Decimal("1.80")) if v.colour == "Antracit" else v
        for v in VARIANTS[TENSIONER_LISTING]
    ]
    listings = {**LISTINGS, TENSIONER_LISTING: dataclasses.replace(
        LISTINGS[TENSIONER_LISTING], variants=variants)}  # fmt: skip
    rows = rows_for("Zatezač (španer)", build_rows(PRICE_MAP, listings, MANUAL, roll_candidates=[]))
    assert {r.note for r in rows} == {"boje nemaju istu cijenu → kupac vidi „cijena na upit“"}
    assert [r.price for r in rows] == ["1,65 KM", "1,80 KM", "1,65 KM"]


def test_one_missing_colour_is_noted_only_on_its_own_row() -> None:
    # R3-1: the found, priced colours keep their price and never say "nije pronađena".
    variants = [v for v in VARIANTS[TENSIONER_LISTING] if v.colour != "Antracit"]
    listings = {**LISTINGS, TENSIONER_LISTING: dataclasses.replace(
        LISTINGS[TENSIONER_LISTING], variants=variants)}  # fmt: skip
    rows = rows_for("Zatezač (španer)", build_rows(PRICE_MAP, listings, MANUAL, roll_candidates=[]))
    by_key = {r.key: r for r in rows}
    assert by_key["boja: Antracit"].note == (
        "varijanta nije pronađena → kupac vidi „cijena na upit“"
    )
    for key in ("boja: Zeleni", "boja: Cinčani"):
        assert by_key[key].price == "1,65 KM"
        assert "nije pronađena" not in by_key[key].note
        assert by_key[key].note == "druga boja nije ispravna → kupac vidi „cijena na upit“"


def test_a_manual_price_of_zero_is_noted_like_the_pricer_sees_it() -> None:
    zero = {
        key: dataclasses.replace(product, price_km=Decimal("0.00"))
        for key, product in MANUAL.items()
    }
    [row] = rows_for("Anker vijak M8×70", build_rows(PRICE_MAP, LISTINGS, zero, roll_candidates=[]))
    assert row.price == ""
    assert row.note.startswith("cijena 0 KM → kupac vidi „cijena na upit“")


def test_a_nan_price_is_never_shown() -> None:
    variants = [
        dataclasses.replace(v, price=Decimal("NaN")) if v.colour == "Zeleni" else v
        for v in VARIANTS[TENSIONER_LISTING]
    ]
    listings = {**LISTINGS, TENSIONER_LISTING: dataclasses.replace(
        LISTINGS[TENSIONER_LISTING], variants=variants)}  # fmt: skip
    rows = rows_for("Zatezač (španer)", build_rows(PRICE_MAP, listings, MANUAL, roll_candidates=[]))
    assert (rows[0].price, rows[0].note) == (
        "",
        "varijanta nema cijenu → kupac vidi „cijena na upit“",
    )
    nan_roll = dataclasses.replace(
        roll_candidate(), variants=[dataclasses.replace(roll_candidate().variants[0],
                                                         price=Decimal("NaN"))]
    )  # fmt: skip
    rolls = rows_for(
        "Varena ograda u roli", build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[nan_roll])
    )
    assert all(r.listing == "nije povezano" for r in rolls)


@pytest.mark.parametrize("unit", ["metar", "m1", "/M", "m"])
def test_roll_units_written_as_metres_are_offered(unit: str) -> None:
    rows = rows_for(
        "Varena ograda u roli",
        build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[roll_candidate(unit=unit)]),
    )
    assert [r.listing for r in rows if r.key == "60 cm"] == ["Ograde u roli antracit"]


def test_every_map_entry_and_unmapped_line_has_a_row() -> None:
    rows = build_rows(PRICE_MAP, LISTINGS, MANUAL, roll_candidates=[])
    expected = sum(len(e.matches) or 1 for e in PRICE_MAP.entries.values())
    assert len(rows) == expected + len(PRICE_MAP.unmapped)


# --- database loaders --------------------------------------------------------------------


@pytest.mark.anyio
async def test_loaders_read_active_listings_and_roll_candidates(database_url: str) -> None:
    async with await build_catalogue(database_url) as conn:
        await add_listing(conn, 900101, "VARENA OGRADA U ROLI", "varena-ograda-u-roli-900101")
        await add_listing(conn, 900102, "BAKLAVA OGRADA U ROLI", "baklava-u-roli-900102")
        await save_products(
            conn,
            [synthetic(900101, "ograda u roli", "roll"), synthetic(900102, "baklava", "b")],
        )
    with psycopg.connect(database_url) as sync:
        sync.read_only = True
        listings = load_listings(sync, [PANEL_LISTING, INACTIVE_PANEL])
        candidates = load_roll_candidates(sync)
    assert set(listings) == {PANEL_LISTING}  # the sold panel is not active
    assert listings[PANEL_LISTING].variants[0].height == "83"
    assert [c.id for c in candidates] == [900101]  # chain link (baklava) is left out


# --- the script's output guard ------------------------------------------------------------

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "price_map_review_sheet.py"


@pytest.mark.parametrize("out", ["/tmp/sheet.md", "data", "data/"])
def test_the_script_writes_only_a_file_under_data(out: str) -> None:
    # Refused before any database connection: exit 2 from argparse.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", out],
        capture_output=True,
        text=True,
        cwd=SCRIPT.parents[1],
        check=False,
    )
    assert result.returncode == 2
    assert "--out must be a file under" in result.stderr
