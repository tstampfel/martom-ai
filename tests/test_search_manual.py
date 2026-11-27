"""Manual products in search (#112): the manual_products.toml entries, indexed like listings.

The catalogue is the shared search one (tests/search_catalogue.py) plus the manual products
synced into `manual_product`. Vectors come from a fake whose meaning the test controls.
"""

# The tests quote the name with the real "×" sign, as in the TOML.
# ruff: noqa: RUF001, RUF003

import logging
import unicodedata
from collections.abc import AsyncIterator, Sequence
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.bom.manual_products import ManualProduct, load_manual_products
from martom.embed_job import run_embedding
from martom.embeddings import EMBEDDING_DIMENSIONS, Embedder
from martom.evals.retrieval import EvalQuestion, run_search_eval, score_run
from martom.extract.store import save_products
from martom.search.hybrid import search_hybrid
from martom.search.keyword import SearchFilters, search_keyword
from martom.search.manual import manual_search_text, sync_manual_products
from martom.search.query_parser import ParsedQuery, QueryParser
from martom.search.rerank import RerankScore
from martom.search.retrieve import retrieve
from martom.search.vector import search_vector
from search_catalogue import TIPLO, add_listing, build_catalogue, synthetic

pytestmark = pytest.mark.anyio

MODEL = "test/anchors"
CONCEPTS = {
    "anker": ("anker", "sidro", "tiplo"),
    "panel": ("panel",),
    "stup": ("stup", "stub"),
}
ANCHOR = "anchor_m8x70"
SAME_NAME = 900301  # a synthetic listing named like the manual anchor
M8X80 = 900302  # a synthetic M8x80 anchor listing


class ConceptEmbeddings(Embeddings):
    @staticmethod
    def _vector(text: str) -> list[float]:
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for axis, stems in enumerate(CONCEPTS.values()):
            if any(stem in plain for stem in stems):
                vector[axis] = 1.0
        vector[-1] = 0.1
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeReranker:
    """Scores a document 0.9 when it contains `favourite`, else 0.1; records what it got."""

    model = "test/reranker"

    def __init__(self, favourite: str) -> None:
        self.favourite = favourite.lower()
        self.documents: list[str] = []

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        self.documents = list(documents)
        scores = [
            RerankScore(i, 0.9 if self.favourite in d.lower() else 0.1)
            for i, d in enumerate(documents)
        ]
        return sorted(scores, key=lambda s: -s.relevance)


def parser_replying(reply: ParsedQuery) -> QueryParser:
    async def run(messages: Sequence[BaseMessage]) -> ParsedQuery:
        return reply

    return QueryParser(RunnableLambda(run), model_name="fake")


def product(key: str = ANCHOR, name: str = "Anker M8×70", price: str = "0.80") -> ManualProduct:
    return ManualProduct(
        key=key,
        name=name,
        price_km=Decimal(price),
        source="Toni 26.11.2026",
        last_confirmed=date(2026, 11, 26),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def embedder() -> Embedder:
    return Embedder(model=MODEL, embeddings=ConceptEmbeddings())


@pytest.fixture
async def conn(database_url: str, embedder: Embedder) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await build_catalogue(database_url) as c:
        await sync_manual_products(c, load_manual_products())
        await run_embedding(c, embedder)
        yield c


# --- the index -------------------------------------------------------------------------


def test_search_text_is_the_name_plus_the_plain_x_spelling_of_its_sizes() -> None:
    # unaccent turns "×" into "*", so "M8×70" alone would never match a customer's "M8x70".
    # Each other word is written once, so it weighs no more than in a listing (R1-0).
    assert manual_search_text(product()) == "Anker M8×70 M8x70"
    assert manual_search_text(product(name="Anker M8 70")) == "Anker M8 70"


async def test_a_manual_name_word_weighs_the_same_as_in_a_listing(
    conn: psycopg.AsyncConnection,
) -> None:
    # Keyword search merges manual and listing scores directly, so the same word must score
    # the same in both (R1-0: a repeated name doubled "anker").
    await add_listing(conn, SAME_NAME, "Anker M8x70", "anker-m8x70-900301")
    await save_products(conn, [synthetic(SAME_NAME, "Anker M8x70", "Anker M8x70")])
    scores = {
        (hit.source, hit.listing_id or hit.manual_key): hit.score
        for hit in await search_keyword(conn, "anker", limit=30)
    }
    assert scores[("manual", ANCHOR)] <= scores[("olx", SAME_NAME)]


async def test_the_manual_m8x70_anchor_does_not_outrank_an_m8x80_listing_for_m8x80(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    await add_listing(conn, M8X80, "Anker M8x80", "anker-m8x80-900302")
    await save_products(conn, [synthetic(M8X80, "anker", "Anker M8x80")])
    await run_embedding(conn, embedder)
    for hits in (
        await search_keyword(conn, "ankeri M8x80 za stupove panelne ograde", limit=30),
        await search_hybrid(conn, "ankeri M8x80 za stupove panelne ograde", embedder=embedder),
    ):
        keys = [hit.key for hit in hits]
        assert keys.index(("olx", M8X80)) < keys.index(("manual", ANCHOR))


async def test_sync_adds_changes_skips_and_removes(database_url: str) -> None:
    async with await build_catalogue(database_url) as c:
        first = await sync_manual_products(c, {ANCHOR: product()})
        assert (first.inserted, first.updated, first.unchanged, first.deleted) == (1, 0, 0, 0)

        again = await sync_manual_products(c, {ANCHOR: product()})
        assert (again.inserted, again.updated, again.unchanged, again.deleted) == (0, 0, 1, 0)

        changed = await sync_manual_products(c, {ANCHOR: product(price="0.90")})
        assert (changed.inserted, changed.updated, changed.unchanged) == (0, 1, 0)
        cursor = await c.execute("SELECT price_km FROM manual_product WHERE key = %s", (ANCHOR,))
        assert (await cursor.fetchone())[0] == Decimal("0.90")

        removed = await sync_manual_products(c, {})
        assert removed.deleted == 1
        cursor = await c.execute("SELECT count(*) FROM manual_product")
        assert (await cursor.fetchone())[0] == 0


async def test_a_changed_name_is_embedded_again_and_an_unchanged_one_is_not(
    database_url: str, embedder: Embedder
) -> None:
    async with await build_catalogue(database_url) as c:
        await sync_manual_products(c, {ANCHOR: product()})
        first = await run_embedding(c, embedder)
        assert (first.manual_embedded, first.manual_unchanged) == (1, 0)
        # A price change doesn't change the embedded text.
        await sync_manual_products(c, {ANCHOR: product(price="0.95")})
        assert (await run_embedding(c, embedder)).manual_unchanged == 1
        await sync_manual_products(c, {ANCHOR: product(name="Anker M8×75")})
        assert (await run_embedding(c, embedder)).manual_embedded == 1


# --- searches --------------------------------------------------------------------------


def manual_keys(hits: Sequence) -> list[str | None]:
    return [hit.manual_key for hit in hits if hit.source == "manual"]


async def test_keyword_search_finds_the_manual_anchor_without_a_link(
    conn: psycopg.AsyncConnection,
) -> None:
    hits = await search_keyword(conn, "Anker M8x70")
    manual = [hit for hit in hits if hit.source == "manual"]
    assert [hit.manual_key for hit in manual] == [ANCHOR]
    assert manual[0].listing_id is None
    assert manual[0].slug is None
    assert manual[0].url is None
    assert manual[0].title == "Anker M8×70"
    # OLX hits keep their source, listing id and link.
    olx = [hit for hit in hits if hit.source == "olx"]
    assert all(hit.listing_id and hit.manual_key is None for hit in olx)


async def test_keyword_search_finds_the_name_as_written(conn: psycopg.AsyncConnection) -> None:
    assert ANCHOR in manual_keys(await search_keyword(conn, "M8×70"))


async def test_keyword_search_tolerates_a_typo_in_a_manual_name(
    conn: psycopg.AsyncConnection,
) -> None:
    assert ANCHOR in manual_keys(await search_keyword(conn, "ankr m8x70"))


async def test_vector_search_finds_the_manual_anchor(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    vector = await embedder.embed_query("sidro za stup")
    hits = await search_vector(conn, vector, MODEL, limit=30)
    assert ANCHOR in manual_keys(hits)
    manual = next(hit for hit in hits if hit.source == "manual")
    assert manual.listing_id is None and manual.url is None


async def test_hybrid_search_finds_the_manual_anchor_and_the_olx_anchors(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    hits = await search_hybrid(conn, "Anker M8x70 za stup sa stopom", embedder=embedder)
    manual = [hit for hit in hits if hit.source == "manual"]
    assert [hit.manual_key for hit in manual] == [ANCHOR]
    assert manual[0].keyword_rank is not None and manual[0].vector_rank is not None
    assert manual[0].url is None
    assert TIPLO in [hit.listing_id for hit in hits]


@pytest.mark.parametrize(
    ("filters", "found"),
    [
        (SearchFilters(max_price=Decimal("1.00")), True),
        (SearchFilters(max_price=Decimal("0.50")), False),
        (SearchFilters(min_price=Decimal("1.00")), False),
        (SearchFilters(product_type="anker"), True),
        (SearchFilters(product_type="panelna ograda"), False),
        # No height or colour is known for a manual product, so it never passes them.
        (SearchFilters(height_cm=150), False),
        (SearchFilters(colour="antracit"), False),
    ],
)
async def test_filters_apply_to_manual_products(
    conn: psycopg.AsyncConnection, embedder: Embedder, filters: SearchFilters, found: bool
) -> None:
    query = "Anker M8x70"
    vector = await embedder.embed_query(query)
    assert (ANCHOR in manual_keys(await search_keyword(conn, query, filters))) is found
    assert (ANCHOR in manual_keys(await search_vector(conn, vector, MODEL, filters))) is found
    hybrid = await search_hybrid(conn, query, filters, embedder=embedder)
    assert (ANCHOR in manual_keys(hybrid)) is found


async def test_a_zero_price_manual_product_never_passes_a_price_filter(
    conn: psycopg.AsyncConnection,
) -> None:
    await sync_manual_products(conn, {ANCHOR: product(price="0")})
    hits = await search_keyword(conn, "Anker M8x70", SearchFilters(max_price=Decimal("1.00")))
    assert ANCHOR not in manual_keys(hits)


async def test_retrieve_reranks_the_manual_anchor_by_its_search_text(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    reranker = FakeReranker("m8x70")
    result = await retrieve(
        conn, "Anker M8x70 za stup sa stopom", embedder=embedder, parser=None, reranker=reranker
    )
    assert "Anker M8×70 M8x70" in reranker.documents
    first = result.hits[0]
    assert (first.source, first.manual_key, first.listing_id, first.url) == (
        "manual",
        ANCHOR,
        None,
        None,
    )
    assert first.relevance == pytest.approx(0.9)
    assert first.matched_filters


async def test_retrieve_checks_parsed_filters_on_manual_top_up_hits(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # A height filter can't hold for a manual product, so it comes back only as a relaxed
    # top-up hit, marked as not matching the filters.
    parser = parser_replying(
        ParsedQuery(height="150 cm", colour=None, min_price=None, max_price=None)
    )
    result = await retrieve(
        conn, "Anker M8x70 za stup 150 cm", embedder=embedder, parser=parser, reranker=None
    )
    manual = [hit for hit in result.hits if hit.source == "manual"]
    assert [hit.manual_key for hit in manual] == [ANCHOR]
    assert not manual[0].matched_filters


async def test_every_eval_mode_scores_the_manual_anchor_by_its_key(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    question = EvalQuestion(
        "m", "Ankeri M8x70 za stup sa stopom", (), "seed", expected_manual_keys=(ANCHOR,)
    )
    for mode in ("keyword", "vector", "hybrid"):
        results = await run_search_eval(conn, [question], mode, embedder=embedder)
        assert ANCHOR in results["m"], mode
        assert score_run([question], results).recall_at_10 == 1.0, mode


async def test_hybrid_still_warns_when_only_manual_products_have_vectors(
    conn: psycopg.AsyncConnection, embedder: Embedder, caplog: pytest.LogCaptureFixture
) -> None:
    # No listing embedded for this model (a model switch whose product embed failed): the
    # manual row's vector must not hide the warning (R1-5).
    await conn.execute("UPDATE product SET embedding = NULL, embedding_model = NULL")
    with caplog.at_level(logging.WARNING, logger="martom.search.hybrid"):
        await search_hybrid(conn, "Anker M8x70", embedder=embedder)
    assert any("vector search returned no listing" in r.getMessage() for r in caplog.records)


async def test_the_price_copy_keeps_every_decimal_of_the_toml_price(database_url: str) -> None:
    # A rounded copy would disagree with the TOML price the customer sees (R1-4).
    async with await build_catalogue(database_url) as c:
        await sync_manual_products(c, {ANCHOR: product(price="0.004")})
        cursor = await c.execute("SELECT price_km FROM manual_product")
        assert (await cursor.fetchone())[0] == Decimal("0.004")
