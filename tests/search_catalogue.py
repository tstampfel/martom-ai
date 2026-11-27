"""The small search catalogue shared by the keyword and hybrid search tests.

The 10 golden extraction records (real listings, tests/fixtures/extraction) plus a few
synthetic products. Imported as a plain module (pytest's `pythonpath` includes tests/).
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from martom.extract.store import ProductRecord, VariantRow, save_products
from martom.migrate import run_migrations

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "extraction"

PANELS = {63996799, 1199211}
BRUSILICA = 52889522
T_POST = 59473597
FARMER = 24449780
KOKOSI = 68378185
KATANAC = 36376333
TIPLO = 31905913

# Synthetic listings (ids far from real OLX ids).
VEZNA_ZICA = 900001  # slash-joined words, for the unaccent fix
INACTIVE_PANEL = 900002  # a sold listing that matches "panelna ograda" perfectly


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def golden_records() -> list[ProductRecord]:
    records = []
    for row in load_fixture("golden.json")["records"].values():
        variants = [
            VariantRow(**{**v, "price": Decimal(v["price"]) if v["price"] else None})
            for v in row["variants"]
        ]
        records.append(ProductRecord(**{**row, "variants": variants}))
    return records


def synthetic(listing_id: int, product_type: str, search_text: str) -> ProductRecord:
    return ProductRecord(
        listing_id=listing_id,
        detail_hash="h",
        product_type=product_type,
        brand=None,
        material=None,
        wire_thickness=None,
        mesh=None,
        colours=[],
        uses=[],
        compatible_with=[],
        synonyms=[],
        extraction={},
        model="test",
        prompt_version="test",
        search_text=search_text,
        variants=[VariantRow("v", "153", None, None, None, "10,00 KM", Decimal("10.00"), "kom")],
    )


async def add_listing(
    conn: psycopg.AsyncConnection,
    listing_id: int,
    title: str,
    slug: str,
    active: bool = True,
    price: float | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, detail_hash, active, price)"
        " VALUES (%s, %s, %s, '{}', 'h', %s, %s)",
        (listing_id, slug, title, active, price),
    )


async def build_catalogue(database_url: str) -> psycopg.AsyncConnection:
    """Migrate the throwaway database and fill it; the caller closes the connection.

    The 10 golden extraction records (real listings) plus VEZNA_ZICA and the sold
    INACTIVE_PANEL.
    """
    run_migrations(database_url, REPO / "migrations")
    c = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    listings = load_fixture("listings.json")["listings"]
    for listing_id, body in listings.items():
        await add_listing(c, int(listing_id), body["title"], body["slug"], price=body.get("price"))
    await add_listing(c, VEZNA_ZICA, "VEZNA ŽICA/POCINČANA 1,2MM", "vezna-zica-900001")
    await add_listing(
        c, INACTIVE_PANEL, "PANELNA OGRADA PANEL", "panelna-ograda-900002", active=False
    )
    records = golden_records()
    records.append(synthetic(VEZNA_ZICA, "vezna žica", "VEZNA ŽICA/POCINČANA 1,2MM | vezna žica"))
    records.append(
        synthetic(
            INACTIVE_PANEL,
            "panelna ograda",
            "PANELNA OGRADA PANEL | panelna ograda | antracit",
        )
    )
    await save_products(c, records)
    return c
