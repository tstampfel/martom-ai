"""Vector and hybrid search (M3-3) against a real Postgres (see conftest).

The catalogue is the keyword tests' one (tests/search_catalogue.py) plus three synthetic
wheelbarrows ("kolica"). Vectors come from `VocabEmbeddings`, a fake whose meaning the test
controls: each concept below is one axis, and a text points along every concept it
mentions. So "tačke" (another word for a wheelbarrow) is close to "KOLICA" although they
share no letters, which is exactly what keyword search can't do.
"""

import asyncio
import logging
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest
from langchain_core.embeddings import Embeddings

from martom.embed_job import run_embedding
from martom.embeddings import EMBEDDING_DIMENSIONS, Embedder, vector_literal
from martom.evals.retrieval import EvalQuestion, issue_102_hits, run_search_eval, score_run
from martom.extract.store import VariantRow, save_products
from martom.search import hybrid
from martom.search.hybrid import search_hybrid
from martom.search.keyword import SearchFilters, search_keyword
from martom.search.vector import search_knowledge, search_vector
from search_catalogue import BRUSILICA, INACTIVE_PANEL, PANELS, T_POST, build_catalogue, synthetic

pytestmark = pytest.mark.anyio

MODEL = "test/vocab"
KOLICA = (900201, 900202, 900203)

# concept -> word stems that mean it (compared without diacritics, lower case)
CONCEPTS = {
    "panel": ("panel",),
    "ograda": ("ograd", "ogard"),  # a real model also places the typo "ogarda" near "ograda"
    "kolica": ("kolica", "tacke"),
    "brusilica": ("brusilic", "flex"),
    "stup": ("stup", "stub"),
    "kokosi": ("kok",),
    "pas": ("psa", "pse"),
}
BIAS_AXIS = EMBEDDING_DIMENSIONS - 1  # every text gets a little of it: no zero vectors


class VocabEmbeddings(Embeddings):
    def __init__(self) -> None:
        self.queries: list[str] = []

    @staticmethod
    def _vector(text: str) -> list[float]:
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for axis, stems in enumerate(CONCEPTS.values()):
            if any(stem in plain for stem in stems):
                vector[axis] = 1.0
        vector[BIAS_AXIS] = 0.1
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def embedder() -> Embedder:
    return Embedder(model=MODEL, embeddings=VocabEmbeddings())


def _price(price: str) -> VariantRow:
    return VariantRow("v", None, None, None, None, f"{price} KM", Decimal(price), "kom")


@pytest.fixture
async def conn(database_url: str, embedder: Embedder) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await build_catalogue(database_url) as c:
        records = []
        for listing_id, price in zip(KOLICA, ("40.00", "120.00", "30.00"), strict=True):
            await c.execute(
                "INSERT INTO olx_listing (id, slug, title, summary, detail_hash)"
                " VALUES (%s, %s, 'KOLICA GRAĐEVINSKA', '{}', 'h')",
                (listing_id, f"kolica-{listing_id}"),
            )
            record = synthetic(listing_id, "kolica", "KOLICA GRAĐEVINSKA | kolica")
            records.append(replace(record, variants=[_price(price)]))
        await save_products(c, records)
        await run_embedding(c, embedder)
        yield c


def ids(hits: list) -> list[int]:
    return [hit.listing_id for hit in hits]


# --- vector search ---------------------------------------------------------------------


async def test_vector_search_finds_a_synonym_keyword_search_cannot(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    assert not set(ids(await search_keyword(conn, "tačke za beton"))) & set(KOLICA)
    vector = await embedder.embed_query("tačke za beton")
    assert set(ids(await search_vector(conn, vector, MODEL, limit=3))) == set(KOLICA)


async def test_vector_search_applies_the_keyword_filters(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    vector = await embedder.embed_query("tačke")
    # Vector search has no relevance floor: it always returns up to `limit` rows, the
    # closest first. The filter removes the 120 KM wheelbarrow from all of them.
    cheap = await search_vector(conn, vector, MODEL, SearchFilters(max_price=Decimal(50)))
    assert set(ids(cheap)[:2]) == {900201, 900203}
    assert 900202 not in ids(cheap)
    posts = await search_vector(
        conn, await embedder.embed_query("stup"), MODEL, SearchFilters(height_cm=150)
    )
    assert T_POST in ids(posts)
    assert not set(ids(posts)) & set(KOLICA)  # no height to read on a wheelbarrow


async def test_vector_search_never_returns_a_sold_listing(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    vector = await embedder.embed_query("panelna ograda")
    # `martom embed` skips sold listings; give this one a vector anyway.
    await conn.execute(
        "UPDATE product SET embedding = %s::vector, embedding_model = %s WHERE listing_id = %s",
        (vector_literal(vector), MODEL, INACTIVE_PANEL),
    )
    assert INACTIVE_PANEL not in ids(await search_vector(conn, vector, MODEL, limit=50))


async def test_vector_search_ignores_vectors_of_another_model(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    vector = await embedder.embed_query("kolica")
    await conn.execute(
        "UPDATE product SET embedding_model = 'other/model' WHERE listing_id = %s", (KOLICA[0],)
    )
    assert KOLICA[0] not in ids(await search_vector(conn, vector, MODEL, limit=50))


# --- hybrid search ---------------------------------------------------------------------


async def test_hybrid_puts_listings_both_searches_agree_on_first(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    hits = await search_hybrid(conn, "Treba mi panelna ograda za dvorište", embedder=embedder)
    assert set(ids(hits)[:2]) == PANELS
    assert hits[0].keyword_rank is not None and hits[0].vector_rank is not None


async def test_hybrid_finds_what_only_the_vector_search_finds(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # Keyword search finds nothing for "tačke"; every hit comes from vector search.
    assert await search_keyword(conn, "tačke") == []
    hits = await search_hybrid(conn, "tačke", embedder=embedder, limit=3)
    assert set(ids(hits)) == set(KOLICA)
    assert all(hit.keyword_rank is None for hit in hits)


async def test_kolica_does_not_return_the_grinder(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # #102: keyword search returns the grinder for "kolica" by trigram alone ("brusilica
    # kutna" is 0.57 similar). Hybrid keeps a trigram-only hit only when vector search
    # also finds the listing, so the false positive goes without touching the 0.55 threshold.
    # candidates=3 because vector search has no relevance floor: on this ~16-product
    # catalogue the default 30 returns every product, the grinder included. This shows the
    # mechanism; whether it holds at the default on the real catalogue is the kolica column
    # of scripts/compare_embeddings.py (R1-3).
    assert BRUSILICA in ids(await search_keyword(conn, "kolica"))
    hits = await search_hybrid(conn, "kolica", embedder=embedder, candidates=3)
    assert BRUSILICA not in ids(hits)
    assert set(ids(hits)) == set(KOLICA)


async def test_panelan_typo_still_finds_the_panels(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # #102: the same rule must keep a real typo match, which vector search confirms. (The
    # fake can't tell the panels from the dog kennel "OD PANELA", so only "kept by both
    # searches, near the top" is checked for the bare typo.)
    hits = await search_hybrid(conn, "panelan", embedder=embedder)
    top = {hit.listing_id: hit for hit in hits[:3]}
    assert top.keys() >= PANELS
    assert all(top[i].keyword_rank is not None for i in PANELS)
    hits = await search_hybrid(conn, "panelan ogarda antracit", embedder=embedder)
    assert set(ids(hits)[:2]) == PANELS


async def test_hybrid_applies_filters_to_both_searches(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    hits = await search_hybrid(
        conn, "kolica", SearchFilters(max_price=Decimal(50)), embedder=embedder
    )
    assert set(ids(hits)[:2]) == {900201, 900203}
    assert 900202 not in ids(hits)


async def test_a_query_of_only_filler_words_finds_nothing_and_embeds_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    fake = VocabEmbeddings()
    assert await search_hybrid(conn, "treba mi", embedder=Embedder(MODEL, fake)) == []
    assert fake.queries == []


async def test_hybrid_hits_carry_the_listing_link(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    hit = (await search_hybrid(conn, "kolica", embedder=embedder))[0]
    assert hit.url == f"https://martom.olx.ba/artikal/kolica-{hit.listing_id}"
    assert hit.title == "KOLICA GRAĐEVINSKA"


# --- knowledge chunks ------------------------------------------------------------------


async def test_knowledge_search_finds_the_closest_chunk(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    for chunk_id, title, body in (
        ("stupovi", "Stupovi", "Stup sa stopom na svaki panel."),
        ("koke", "Mreža za kokoši", "Za koke uzmite mrežu od 150 cm."),
    ):
        await conn.execute(
            "INSERT INTO knowledge_chunk (id, title, topic, sources, status, body,"
            " source_path, content_hash) VALUES (%s, %s, 't', '{x}', 'draft', %s, 'f.md', 'h')",
            (chunk_id, title, body),
        )
    await run_embedding(conn, embedder)
    hits = await search_knowledge(conn, "ograda za kokoši", embedder=embedder, limit=1)
    assert [(h.id, h.title, h.status) for h in hits] == [("koke", "Mreža za kokoši", "draft")]
    assert hits[0].body.startswith("Za koke")


# --- eval runner -----------------------------------------------------------------------


async def test_the_eval_runs_each_search_mode(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    questions = [EvalQuestion("q", "tačke", tuple(KOLICA), "seed")]
    assert await run_search_eval(conn, questions, "keyword") == {"q": []}
    vector = await run_search_eval(conn, questions, "vector", embedder=embedder, limit=3)
    hybrid = await run_search_eval(conn, questions, "hybrid", embedder=embedder, limit=3)
    assert set(vector["q"]) == set(hybrid["q"]) == set(KOLICA)
    with pytest.raises(ValueError, match="embedder"):
        await run_search_eval(conn, questions, "hybrid")
    with pytest.raises(ValueError, match="mode"):
        await run_search_eval(conn, questions, "magic", embedder=embedder)


async def test_the_forbidden_hit_rate_scores_every_search_mode(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # #102: "kolica" must not return the grinder. In every mode the rate follows the ranked
    # ids that mode returned. Keyword search does return the grinder (the trigram false
    # positive on the fixture text "brusilica kutna"); whether vector and hybrid search do
    # depends on this small catalogue (every listing fits in the vector candidates), so
    # only the rate's agreement with their results is pinned here.
    questions = [
        EvalQuestion(
            "kolica", "kolica", tuple(KOLICA), "seed", must_not_include_listing_ids=(BRUSILICA,)
        )
    ]
    for mode in ("keyword", "vector", "hybrid"):
        results = await run_search_eval(conn, questions, mode, embedder=embedder)
        scores = score_run(questions, results)
        returned = BRUSILICA in results["kolica"][:10]
        assert scores.forbidden_checked == 1, mode
        assert scores.forbidden_hit_ids == (("kolica",) if returned else ()), mode
        assert scores.forbidden_hit_rate == (1.0 if returned else 0.0), mode
        if mode == "keyword":
            assert returned


# --- when vector search can't help (R1-1, R1-2) ------------------------------------------


class FailingEmbeddings(VocabEmbeddings):
    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("provider down")


class SlowEmbeddings(VocabEmbeddings):
    async def aembed_query(self, text: str) -> list[float]:
        await asyncio.sleep(5)
        return self._vector(text)


async def test_a_failing_query_embedding_falls_back_to_keyword_results(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    embedder = Embedder(MODEL, FailingEmbeddings())
    with caplog.at_level(logging.WARNING, logger="martom.search.hybrid"):
        hits = await search_hybrid(conn, "panelan ogarda", embedder=embedder)
    # Typo matches stay: there is no vector search to confirm or reject them. (Both words
    # are typos, so full text matches nothing; keyword alone ranks the panels first.)
    assert set(ids(hits)[:2]) == PANELS
    assert all(hit.vector_rank is None for hit in hits)
    assert "keyword results only" in caplog.text


async def test_a_slow_query_embedding_times_out_into_keyword_results(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid, "QUERY_EMBED_TIMEOUT_SECONDS", 0.05)
    hits = await search_hybrid(conn, "panelna ograda", embedder=Embedder(MODEL, SlowEmbeddings()))
    assert set(ids(hits)[:2]) == PANELS
    assert all(hit.vector_rank is None for hit in hits)  # keyword only: the timeout fired


async def test_typo_matches_stay_when_the_model_has_no_vectors_yet(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    # EMBEDDING_MODEL changed but `martom embed` hasn't run: vector search finds nothing.
    embedder = Embedder("test/not-embedded-yet", VocabEmbeddings())
    with caplog.at_level(logging.WARNING, logger="martom.search.hybrid"):
        hits = await search_hybrid(conn, "panelan ogarda", embedder=embedder)
    assert set(ids(hits)[:2]) == PANELS
    assert [r.embedding_model for r in caplog.records] == ["test/not-embedded-yet"]


async def test_a_typo_match_on_a_listing_without_a_vector_stays(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # A listing extracted since the last `martom embed` has no vector: vector search can't
    # have found it, so it can't be judged and is kept. The grinder has a vector that
    # vector search didn't return for "kolica", so it still goes.
    await conn.execute("UPDATE product SET embedding = NULL WHERE listing_id = 1199211")
    assert 1199211 in ids(await search_hybrid(conn, "panelan", embedder=embedder))
    hits = await search_hybrid(conn, "kolica", embedder=embedder, candidates=3)
    assert BRUSILICA not in ids(hits)


async def test_without_fallback_a_failing_query_embedding_raises(
    conn: psycopg.AsyncConnection,
) -> None:
    # Evals must never score keyword results as hybrid (R2-1).
    with pytest.raises(RuntimeError, match="provider down"):
        await search_hybrid(
            conn, "panelan", embedder=Embedder(MODEL, FailingEmbeddings()), fallback=False
        )


async def test_without_fallback_a_slow_query_embedding_is_waited_for(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid, "QUERY_EMBED_TIMEOUT_SECONDS", 0.05)

    class SlightlySlow(VocabEmbeddings):
        async def aembed_query(self, text: str) -> list[float]:
            await asyncio.sleep(0.2)
            return self._vector(text)

    hits = await search_hybrid(
        conn, "panelna ograda", embedder=Embedder(MODEL, SlightlySlow()), fallback=False
    )
    assert hits[0].vector_rank is not None


async def test_the_hybrid_eval_fails_loudly_instead_of_scoring_keyword_results(
    conn: psycopg.AsyncConnection,
) -> None:
    questions = [EvalQuestion("q", "panelan", tuple(PANELS), "seed")]
    with pytest.raises(RuntimeError, match="provider down"):
        await run_search_eval(
            conn, questions, "hybrid", embedder=Embedder(MODEL, FailingEmbeddings())
        )


async def test_the_102_checks_fail_loudly_instead_of_using_keyword_results(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # compare_embeddings.py's kolica/panelan columns (R3-1): a keyword fallback would show
    # the grinder for "kolica" because the embedding failed, not because the rule did.
    kolica, panelan = await issue_102_hits(conn, embedder)
    assert set(kolica) >= set(KOLICA) and set(panelan) >= PANELS
    with pytest.raises(RuntimeError, match="provider down"):
        await issue_102_hits(conn, Embedder(MODEL, FailingEmbeddings()))
