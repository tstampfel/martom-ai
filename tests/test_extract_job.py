"""`run_extraction` against a real Postgres: what gets extracted, saved and recorded.

The model is a fake (`RunnableLambda`), so no Claude calls; the database is a throwaway
one per test (see conftest.py).
"""

import re
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.extract.extractor import Extractor
from martom.extract.job import run_extraction
from martom.extract.prompt import PROMPT_VERSION
from martom.extract.schema import ExtractedProduct, ExtractedVariant
from martom.migrate import run_migrations

pytestmark = pytest.mark.anyio

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[psycopg.AsyncConnection]:
    run_migrations(database_url, MIGRATIONS)
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as c:
        yield c


async def add_listing(
    conn: psycopg.AsyncConnection,
    listing_id: int,
    *,
    detail_hash: str | None = "hash-1",
    active: bool = True,
    title: str = "STUP METALNI",
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, description_text, display_price,"
        " detail_hash, active) VALUES (%s, %s, %s, '{}', %s, '10 KM', %s, %s)",
        (listing_id, f"stup-{listing_id}", title, "Stup H-100 cm: 10,00 KM", detail_hash, active),
    )


def product(price: str | None = "10,00 KM") -> ExtractedProduct:
    return ExtractedProduct(
        product_type="stup za ogradu", brand=None, material="pocinčani", wire_thickness=None,
        mesh=None, colours=["zelena"], uses=["ograda"], compatible_with=[], synonyms=[],
        variants=[
            ExtractedVariant(label="Stup H-100 cm: 10,00 KM", height="H-100 cm", length=None,
                             colour=None, size=None, price=price, unit="kom"),
            ExtractedVariant(label="Stup H-150 cm", height="H-150 cm", length=None,
                             colour=None, size=None, price=None, unit=None),
        ],
    )  # fmt: skip


class FakeModel:
    """Records which listings it was asked about; can be told to fail some."""

    def __init__(self, failing: set[int] | None = None, price: str = "10,00 KM") -> None:
        self.asked: list[int] = []
        self.failing = failing or set()
        self.price = price

    def __call__(self, messages: Sequence[BaseMessage]) -> ExtractedProduct:
        match = re.search(r'<listing id="(\d+)">', messages[1].text)
        assert match is not None
        listing_id = int(match.group(1))
        self.asked.append(listing_id)
        if listing_id in self.failing:
            raise RuntimeError("model unavailable")
        return product(self.price)


def extractor(model: FakeModel) -> Extractor:
    return Extractor(RunnableLambda(model), model_name="fake-haiku", retry_wait=False)


async def fetch(conn: psycopg.AsyncConnection, query: str, *args: object) -> list[tuple]:
    cursor = await conn.execute(query, args)
    return await cursor.fetchall()


async def test_extracts_new_listings_into_product_and_variants(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)

    stats = await run_extraction(conn, extractor(FakeModel()))

    assert (stats.status, stats.seen, stats.extracted, stats.errors) == ("succeeded", 2, 2, 0)
    [(listing_id, detail_hash, product_type, colours, model, version, search_text)] = await fetch(
        conn,
        "SELECT listing_id, detail_hash, product_type, colours, model, prompt_version,"
        " search_text FROM product WHERE listing_id = 1",
    )
    assert (listing_id, detail_hash, product_type, colours) == (1, "hash-1", "stup za ogradu",
                                                                ["zelena"])  # fmt: skip
    assert (model, version) == ("fake-haiku", PROMPT_VERSION)
    assert search_text.startswith("STUP METALNI | stup za ogradu | zelena | H-100 cm H-150 cm")
    variants = await fetch(
        conn,
        "SELECT position, label, height, price_text, price, unit FROM product_variant"
        " WHERE listing_id = 1 ORDER BY position",
    )
    assert variants == [
        (0, "Stup H-100 cm: 10,00 KM", "H-100 cm", "10,00 KM", Decimal("10.00"), "kom"),
        (1, "Stup H-150 cm", "H-150 cm", None, None, None),  # no price stays no price
    ]
    # The generated tsvector finds the product through the `martom` config; "stub" is in
    # search_text only because of the stup/stub synonym pair.
    [(found,)] = await fetch(
        conn,
        "SELECT count(*) FROM product WHERE search_tsv @@ plainto_tsquery('martom', 'stub')",
    )
    assert found == 2
    [(kind, status, seen, extracted, finished)] = await fetch(
        conn, "SELECT kind, status, seen, extracted, finished_at IS NOT NULL FROM sync_run"
    )
    assert (kind, status, seen, extracted, finished) == ("extraction", "succeeded", 2, 2, True)


async def test_re_extracts_only_when_the_detail_hash_changed(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)
    await run_extraction(conn, extractor(FakeModel()))

    unchanged = FakeModel()
    stats = await run_extraction(conn, extractor(unchanged))
    assert unchanged.asked == []
    assert (stats.seen, stats.extracted) == (0, 0)

    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2' WHERE id = 2")
    changed = FakeModel(price="12,50 KM")
    await run_extraction(conn, extractor(changed))

    assert changed.asked == [2]
    rows = await fetch(
        conn,
        "SELECT p.listing_id, p.detail_hash, v.price FROM product p"
        " JOIN product_variant v USING (listing_id) WHERE v.position = 0 ORDER BY 1",
    )
    assert rows == [(1, "hash-1", Decimal("10.00")), (2, "hash-2", Decimal("12.50"))]
    # The old variants were replaced, not added to.
    [(count,)] = await fetch(conn, "SELECT count(*) FROM product_variant WHERE listing_id = 2")
    assert count == 2


async def test_skips_inactive_and_never_fetched_listings(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2, active=False)  # sold: not in active_olx_listing
    await add_listing(conn, 3, detail_hash=None)  # detail never fetched yet
    model = FakeModel()

    await run_extraction(conn, extractor(model))

    assert model.asked == [1]


async def test_a_failed_listing_keeps_its_old_product_and_is_retried_next_run(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)
    await run_extraction(conn, extractor(FakeModel()))
    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")

    stats = await run_extraction(conn, extractor(FakeModel(failing={2})))

    assert (stats.status, stats.extracted, stats.errors) == ("partial", 1, 1)
    assert stats.failed_listing_ids == [2]
    hashes = await fetch(conn, "SELECT listing_id, detail_hash FROM product ORDER BY 1")
    assert hashes == [(1, "hash-2"), (2, "hash-1")]
    [(status, errors, failed)] = await fetch(
        conn, "SELECT status, errors, failed_listing_ids FROM sync_run ORDER BY id DESC LIMIT 1"
    )
    assert (status, errors, failed) == ("partial", 1, [2])

    retry = FakeModel()
    await run_extraction(conn, extractor(retry))
    assert retry.asked == [2]


async def test_all_re_extracts_every_listing_and_limit_caps_the_run(
    conn: psycopg.AsyncConnection,
) -> None:
    for listing_id in (1, 2, 3):
        await add_listing(conn, listing_id)
    await run_extraction(conn, extractor(FakeModel()))

    everything = FakeModel()
    await run_extraction(conn, extractor(everything), force_all=True)
    assert sorted(everything.asked) == [1, 2, 3]

    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")
    capped = FakeModel()
    stats = await run_extraction(conn, extractor(capped), limit=2)
    assert sorted(capped.asked) == [1, 2]
    assert stats.seen == 2


async def test_a_database_error_marks_the_run_failed(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)
    # Drop the variant table, so saving breaks after the model call.
    await conn.execute("DROP TABLE product_variant")

    stats = await run_extraction(conn, extractor(FakeModel()))

    assert stats.status == "failed"
    [(status, error)] = await fetch(conn, "SELECT status, error FROM sync_run")
    assert status == "failed"
    assert "product_variant" in error
