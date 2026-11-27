"""`martom embed` (M3-3) against a real Postgres (see conftest): only changed texts are embedded."""

from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from langchain_core.embeddings import Embeddings

from martom.embed_job import knowledge_text, run_embedding
from martom.embeddings import EMBEDDING_DIMENSIONS, Embedder, text_hash
from martom.extract.store import ProductRecord, VariantRow, save_products
from martom.migrate import run_migrations

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingEmbeddings(Embeddings):
    """A constant unit vector per text; remembers every text it was asked to embed."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the embed job never embeds queries")


def _product(listing_id: int, search_text: str) -> ProductRecord:
    return ProductRecord(
        listing_id=listing_id,
        detail_hash="h",
        product_type="panelna ograda",
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


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[psycopg.AsyncConnection]:
    run_migrations(database_url, REPO / "migrations")
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as c:
        for listing_id, active in ((1, True), (2, True), (3, False)):
            await c.execute(
                "INSERT INTO olx_listing (id, slug, title, summary, detail_hash, active)"
                " VALUES (%s, %s, 'T', '{}', 'h', %s)",
                (listing_id, f"t-{listing_id}", active),
            )
        await save_products(
            c, [_product(1, "PANEL A"), _product(2, "PANEL B"), _product(3, "SOLD PANEL")]
        )
        await c.execute(
            "INSERT INTO knowledge_chunk (id, title, topic, sources, status, body,"
            " source_path, content_hash) VALUES"
            " ('stupovi', 'Stupovi', 'posts', '{x}', 'draft', 'Stup na svaki panel.',"
            " 'stupovi.md', 'h')"
        )
        yield c


async def _stored(conn: psycopg.AsyncConnection, table: str, key: str) -> dict:
    cursor = await conn.execute(
        f"SELECT {key}, embedding_model, embedding_hash, vector_dims(embedding)"
        f" FROM {table} WHERE embedding IS NOT NULL"
    )
    return {row[0]: row[1:] for row in await cursor.fetchall()}


async def test_embeds_active_products_and_knowledge_chunks(
    conn: psycopg.AsyncConnection,
) -> None:
    fake = RecordingEmbeddings()
    stats = await run_embedding(conn, Embedder(model="test/a", embeddings=fake))

    assert (stats.products_embedded, stats.chunks_embedded) == (2, 1)
    assert sorted(fake.texts) == sorted(
        ["PANEL A", "PANEL B", knowledge_text("Stupovi", "Stup na svaki panel.")]
    )
    products = await _stored(conn, "product", "listing_id")
    assert products == {
        1: ("test/a", text_hash("test/a", "PANEL A"), EMBEDDING_DIMENSIONS),
        2: ("test/a", text_hash("test/a", "PANEL B"), EMBEDDING_DIMENSIONS),
    }  # the sold listing (3) is never embedded
    chunks = await _stored(conn, "knowledge_chunk", "id")
    assert chunks["stupovi"][0] == "test/a"


async def test_a_second_run_embeds_nothing(conn: psycopg.AsyncConnection) -> None:
    await run_embedding(conn, Embedder(model="test/a", embeddings=RecordingEmbeddings()))
    fake = RecordingEmbeddings()
    stats = await run_embedding(conn, Embedder(model="test/a", embeddings=fake))
    assert fake.texts == []
    assert (stats.products_embedded, stats.products_unchanged) == (0, 2)
    assert (stats.chunks_embedded, stats.chunks_unchanged) == (0, 1)


async def test_only_changed_texts_are_embedded_again(conn: psycopg.AsyncConnection) -> None:
    await run_embedding(conn, Embedder(model="test/a", embeddings=RecordingEmbeddings()))
    # A re-extraction changed one product's search text, and Toni edited the chunk.
    await save_products(conn, [_product(2, "PANEL B 153 cm")])
    await conn.execute("UPDATE knowledge_chunk SET body = 'Stup na svaka dva panela.'")

    fake = RecordingEmbeddings()
    await run_embedding(conn, Embedder(model="test/a", embeddings=fake))
    assert fake.texts == [
        "PANEL B 153 cm",
        knowledge_text("Stupovi", "Stup na svaka dva panela."),
    ]


async def test_a_new_model_embeds_everything_again(conn: psycopg.AsyncConnection) -> None:
    await run_embedding(conn, Embedder(model="test/a", embeddings=RecordingEmbeddings()))
    fake = RecordingEmbeddings()
    stats = await run_embedding(conn, Embedder(model="test/b", embeddings=fake))
    assert (stats.products_embedded, stats.chunks_embedded) == (2, 1)
    assert {row[0] for row in (await _stored(conn, "product", "listing_id")).values()} == {"test/b"}


async def test_migration_007_runs_twice(database_url: str) -> None:
    run_migrations(database_url, REPO / "migrations")
    with psycopg.connect(database_url) as c:
        c.execute("DELETE FROM schema_migrations WHERE filename LIKE '007_%'")
        c.commit()
    run_migrations(database_url, REPO / "migrations")  # the statements themselves re-run
