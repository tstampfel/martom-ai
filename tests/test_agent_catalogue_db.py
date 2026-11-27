"""PostgresCatalogue against the fixture catalogue (the 10 real golden listings)."""

from decimal import Decimal

import pytest
from tests.search_catalogue import INACTIVE_PANEL, build_catalogue

from martom.agent.catalogue import PostgresCatalogue

pytestmark = pytest.mark.anyio

PANEL_ID = 63996799


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_get_product_returns_variants_with_database_prices(database_url: str) -> None:
    async with await build_catalogue(database_url) as conn:
        product = await PostgresCatalogue(conn).get_product(PANEL_ID)
    assert product is not None
    assert product.listing_id == PANEL_ID
    assert product.url is not None
    assert product.url.endswith(f"-{PANEL_ID}")
    first = product.variants[0]
    assert first.label.startswith("3D Panelna ograda 83 x 200 cm")
    assert first.price == Decimal("27.00")
    assert first.height == "83"


async def test_inactive_or_unknown_listing_is_not_found(database_url: str) -> None:
    async with await build_catalogue(database_url) as conn:
        catalogue = PostgresCatalogue(conn)
        assert await catalogue.get_product(INACTIVE_PANEL) is None
        assert await catalogue.get_product(1) is None


async def test_variant_heights_are_parsed_and_sorted(database_url: str) -> None:
    async with await build_catalogue(database_url) as conn:
        heights = await PostgresCatalogue(conn).variant_heights(PANEL_ID)
    # Panel heights plus the post heights (85 cm, ...) written in the same listing.
    assert {83, 85, 103, 153, 203} <= set(heights)
    assert heights == sorted(set(heights))
