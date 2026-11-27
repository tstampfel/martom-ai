"""PostgresBomPricer (#125) against a throwaway database holding the mapped listings."""

# ruff: noqa: RUF001

import dataclasses
import logging
from decimal import Decimal

import psycopg
import pytest

from bom_pricing_fixture import PANEL_LISTING, T_POST_LISTING, VARIANTS
from martom.agent.pricer import PostgresBomPricer
from martom.agent.tools import build_bom_event
from martom.bom.common import BomLine, Item
from martom.bom.panel import panel_fence_bom
from martom.bom.pricing import load_price_map
from martom.bom.roll import roll_fence_bom
from martom.extract.store import ProductRecord, save_products
from martom.migrate import run_migrations
from search_catalogue import REPO, add_listing

pytestmark = pytest.mark.anyio

# The shipped map is a draft, which prices nothing (Tomislav, R1-7); these tests price
# through the same entries marked reviewed.
REVIEWED = dataclasses.replace(load_price_map(), status="reviewed")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def record(listing_id: int) -> ProductRecord:
    return ProductRecord(
        listing_id=listing_id,
        detail_hash="h",
        product_type="ograda",
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
        search_text="ograda",
        variants=VARIANTS[listing_id],
    )


async def build(database_url: str, *, inactive: frozenset[int] = frozenset()):
    run_migrations(database_url, REPO / "migrations")
    conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    for listing_id in VARIANTS:
        await add_listing(
            conn, listing_id, f"Oglas {listing_id}", f"oglas-{listing_id}",
            active=listing_id not in inactive,
        )  # fmt: skip
    await save_products(conn, [record(listing_id) for listing_id in VARIANTS])
    return conn


async def test_panel_bom_is_priced_from_the_database(database_url: str) -> None:
    async with await build(database_url) as conn:
        pricer = PostgresBomPricer(conn, price_map=REVIEWED)
        lines = panel_fence_bom(sides_m=[25], panel_height_cm=153)
        event = await build_bom_event(lines, pricer, summary="s", extra_lines=[], notes=[])
    # fence-rules.md: 905,10 KM with the OLX prices of 26 Nov 2026.
    assert event.total == "905.10"
    assert event.unpriced_count == 0


async def test_roll_bom_prices_mapped_lines_and_leaves_the_roll_on_request(
    database_url: str,
) -> None:
    async with await build(database_url) as conn:
        pricer = PostgresBomPricer(conn, price_map=REVIEWED)
        bom = roll_fence_bom(sides_m=[30], roll_height_cm=125, post_model="foot_plate")
        prices = {line.item: await pricer.unit_price(line) for line in bom.lines_with_wire()}
    assert prices == {
        Item.ROLL_WELDED: None,
        Item.POST_FOOT_PLATE: Decimal("16.50"),
        Item.BRACE_T_POST: Decimal("10.00"),
        Item.TENSIONER: Decimal("1.65"),
        Item.ANCHOR_M8X70: Decimal("0.80"),
        Item.TENSION_WIRE: Decimal("18.00"),
        Item.WIRE_HOLDER: Decimal("0.40"),
    }


async def test_inactive_listing_is_on_request(database_url: str) -> None:
    async with await build(database_url, inactive=frozenset({T_POST_LISTING})) as conn:
        pricer = PostgresBomPricer(conn, price_map=REVIEWED)
        line = BomLine(Item.POST_T, "T-stup 150 cm", 10, size="150 cm")
        assert await pricer.unit_price(line) is None


async def test_variant_that_lost_its_price_is_on_request(database_url: str) -> None:
    async with await build(database_url) as conn:
        await conn.execute(
            "UPDATE product_variant SET price = NULL WHERE listing_id = %s AND height = '153'"
            " AND length = '250 cm'",
            (PANEL_LISTING,),
        )
        line = BomLine(Item.PANEL, "Panel 153 × 250 cm", 10, size="153 × 250 cm")
        assert await PostgresBomPricer(conn, price_map=REVIEWED).unit_price(line) is None


async def test_unmapped_and_on_request_lines_never_query_a_price(database_url: str) -> None:
    async with await build(database_url) as conn:
        pricer = PostgresBomPricer(conn, price_map=REVIEWED)
        on_request = BomLine(
            Item.PANEL, "Panel", 1, size="153 × 250 cm", unit_price_on_request=True
        )
        unknown_size = BomLine(Item.PANEL, "Panel", 1, size="153 × 300 cm")
        assert await pricer.unit_price(on_request) is None
        assert await pricer.unit_price(unknown_size) is None


async def test_a_database_error_makes_only_that_line_on_request(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    # R1-2: the BOM still comes back; the failed lookups are "cijena na upit".
    conn = await build(database_url)
    pricer = PostgresBomPricer(conn, price_map=REVIEWED)
    await conn.close()  # every query now raises psycopg.OperationalError
    lines = panel_fence_bom(sides_m=[25], panel_height_cm=153)
    with caplog.at_level(logging.WARNING, logger="martom.agent.pricer"):
        event = await build_bom_event(lines, pricer, summary="s", extra_lines=[], notes=[])
    assert [line.price_on_request for line in event.lines] == [True] * len(lines)
    assert event.total is None
    record = next(r for r in caplog.records if r.message == "bom price lookup failed")
    assert record.listing_id == PANEL_LISTING  # type: ignore[attr-defined]


async def test_needs_an_autocommit_connection(database_url: str) -> None:
    # R1-5: a connection idle in a transaction would hold locks between chats, and one
    # error would abort every later lookup.
    run_migrations(database_url, REPO / "migrations")
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        with pytest.raises(ValueError, match="autocommit"):
            PostgresBomPricer(conn)


async def test_a_draft_map_prices_nothing_and_warns_once(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    # R1-7 (Tomislav): until Tomislav and Toni review the map, every line is on request.
    async with await build(database_url) as conn:
        with caplog.at_level(logging.WARNING, logger="martom.agent.pricer"):
            pricer = PostgresBomPricer(conn)  # the shipped map, status "draft"
            lines = panel_fence_bom(sides_m=[25], panel_height_cm=153)
            event = await build_bom_event(lines, pricer, summary="s", extra_lines=[], notes=[])
    assert pricer.price_map.status == "draft"
    assert event.total is None
    assert all(line.price_on_request for line in event.lines)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.message for r in warnings] == ["price map is a draft: every BOM line is on request"]
