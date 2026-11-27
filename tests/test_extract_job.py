"""`run_extraction` against a real Postgres: what gets extracted, saved and recorded.

The model is a fake (`RunnableLambda`), so no Claude calls; the database is a throwaway
one per test (see conftest.py).
"""

import re
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx2
import psycopg
import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.extract.extractor import Extractor
from martom.extract.job import BATCH_SIZE, run_extraction
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


def product(price: str | None = "10,00 KM", label: str = "Stup") -> ExtractedProduct:
    return ExtractedProduct(
        product_type="stup za ogradu", brand=None, material="pocinčani", wire_thickness=None,
        mesh=None, colours=["zelena"], uses=["ograda"], compatible_with=[], synonyms=[],
        regional_synonyms=[], variants=[
            ExtractedVariant(label=f"{label} H-100 cm: 10,00 KM", height="H-100 cm", length=None,
                             colour=None, size=None, price=price, unit="kom"),
            ExtractedVariant(label=f"{label} H-150 cm", height="H-150 cm", length=None,
                             colour=None, size=None, price=None, unit=None),
        ],
    )  # fmt: skip


class FakeModel:
    """Records which listings it was asked about; can be told to fail some."""

    def __init__(
        self, failing: set[int] | None = None, price: str = "10,00 KM", label: str = "Stup"
    ) -> None:
        self.label = label
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
        return product(self.price, self.label)


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


async def test_a_run_where_every_listing_fails_is_failed_not_partial(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)

    stats = await run_extraction(conn, extractor(FakeModel(failing={1, 2})))

    assert (stats.status, stats.extracted, stats.errors) == ("failed", 0, 2)
    assert stats.error is not None
    assert "no listing was extracted" in stats.error
    [(status, failed)] = await fetch(conn, "SELECT status, failed_listing_ids FROM sync_run")
    assert (status, failed) == ("failed", [1, 2])


async def test_an_outage_stops_the_run_after_a_batch_of_consecutive_failures(
    conn: psycopg.AsyncConnection,
) -> None:
    # 100 listings, the model is down: the run stops after the first batch (40) instead of
    # working through every listing.
    for listing_id in range(1, 101):
        await add_listing(conn, listing_id)
    model = FakeModel(failing=set(range(1, 101)))

    stats = await run_extraction(conn, extractor(model))

    assert stats.status == "failed"
    assert len(model.asked) == BATCH_SIZE
    assert stats.error is not None
    assert "consecutive" in stats.error


async def test_a_non_retryable_api_error_fails_the_run_at_once(
    conn: psycopg.AsyncConnection,
) -> None:
    for listing_id in range(1, 11):
        await add_listing(conn, listing_id)

    def bad_key(_: Sequence[BaseMessage]) -> ExtractedProduct:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx2.Response(401, request=request)
        raise anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)

    stats = await run_extraction(
        conn, Extractor(RunnableLambda(bad_key), model_name="fake", retry_wait=False)
    )

    assert stats.status == "failed"
    assert stats.error is not None
    assert "AuthenticationError" in stats.error
    [(count,)] = await fetch(conn, "SELECT count(*) FROM product")
    assert count == 0


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


async def test_review_checks_run_after_extraction(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)

    stats = await run_extraction(conn, extractor(FakeModel()))

    assert stats.status == "succeeded"
    assert stats.review is not None and stats.review.new >= 1
    # product() lists "Stup H-150 cm" without a price.
    rows = await fetch(conn, "SELECT kind, listing_id FROM review_issue ORDER BY kind")
    assert ("height_without_price", 1) in rows

    # Rerunning (nothing left to extract) keeps one row per problem.
    await run_extraction(conn, extractor(FakeModel()))
    assert await fetch(conn, "SELECT kind, listing_id FROM review_issue ORDER BY kind") == rows


async def test_a_review_check_error_makes_the_run_partial(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_listing(conn, 1)

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("checks broke")

    monkeypatch.setattr("martom.extract.job.run_review_checks", broken)
    stats = await run_extraction(conn, extractor(FakeModel()))

    # The products were saved; only the review queue is out of date.
    assert (stats.status, stats.extracted) == ("partial", 1)
    [(status, error)] = await fetch(conn, "SELECT status, error FROM sync_run")
    assert status == "partial"
    assert "review checks" in error and "checks broke" in error


async def test_a_broken_price_map_makes_the_run_partial(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R2-1: the other review checks are saved, the run is 'partial', not 'succeeded'.
    await add_listing(conn, 1)

    def broken_map(*_args: object, **_kwargs: object) -> None:
        raise ValueError("price map: status must be 'draft' or 'reviewed'")

    monkeypatch.setattr("martom.review.job.load_price_map", broken_map)
    stats = await run_extraction(conn, extractor(FakeModel()))

    assert (stats.status, stats.extracted) == ("partial", 1)
    [(status, error)] = await fetch(conn, "SELECT status, error FROM sync_run")
    assert status == "partial"
    assert "price map invalid" in error
    rows = await fetch(conn, "SELECT kind, listing_id FROM review_issue")
    assert ("height_without_price", 1) in rows


async def open_issue_listings(conn: psycopg.AsyncConnection) -> list[tuple]:
    return await fetch(
        conn,
        "SELECT listing_id, status FROM review_issue WHERE kind = 'height_without_price'"
        " ORDER BY listing_id",
    )


async def test_ignored_issue_stays_ignored_after_re_extraction_with_a_reworded_label(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await run_extraction(conn, extractor(FakeModel(label="Stup")))
    await conn.execute("UPDATE review_issue SET status = 'ignored'")
    before = await fetch(conn, "SELECT id, kind, status FROM review_issue ORDER BY id")

    # The listing changes on OLX; the model words its labels differently this time.
    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")
    stats = await run_extraction(conn, extractor(FakeModel(label="Metalni stub")))

    assert stats.extracted == 1
    [(label,)] = await fetch(conn, "SELECT label FROM product_variant WHERE position = 1")
    assert label == "Metalni stub H-150 cm"
    assert await fetch(conn, "SELECT id, kind, status FROM review_issue ORDER BY id") == before
    assert stats.review is not None and stats.review.new == 0


async def test_a_limit_run_keeps_the_issues_of_listings_it_did_not_extract(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)
    await run_extraction(conn, extractor(FakeModel()))
    assert await open_issue_listings(conn) == [(1, "open"), (2, "open")]

    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")
    model = FakeModel()
    await run_extraction(conn, extractor(model), limit=1)

    assert model.asked == [1]
    assert await open_issue_listings(conn) == [(1, "open"), (2, "open")]


async def test_a_partial_run_keeps_the_issues_of_listings_that_failed(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2)
    await run_extraction(conn, extractor(FakeModel()))

    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")
    stats = await run_extraction(conn, extractor(FakeModel(failing={2})))

    assert stats.status == "partial"
    # Listing 2 kept its old product, so its problem is still found and stays open.
    assert await open_issue_listings(conn) == [(1, "open"), (2, "open")]


async def test_a_failed_run_leaves_the_review_queue_alone(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)
    await run_extraction(conn, extractor(FakeModel()))
    before = await fetch(conn, "SELECT id, status, last_seen_at FROM review_issue ORDER BY id")

    await conn.execute("UPDATE olx_listing SET detail_hash = 'hash-2'")
    stats = await run_extraction(conn, extractor(FakeModel(failing={1})))

    assert stats.status == "failed" and stats.review is None
    after = await fetch(conn, "SELECT id, status, last_seen_at FROM review_issue ORDER BY id")
    assert after == before
