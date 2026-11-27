"""Keyword search (M3-2) against a real Postgres (see conftest).

The catalogue is the 10 golden extraction records (real listings, tests/fixtures/extraction)
plus a few small synthetic products for the cases the golden set doesn't cover.
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from martom.evals.retrieval import EvalQuestion, run_keyword_eval
from martom.extract.store import ProductRecord, VariantRow, save_products
from martom.migrate import run_migrations
from martom.search.keyword import KeywordHit, SearchFilters, search_keyword, strip_filler_words

pytestmark = pytest.mark.anyio

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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _golden_records() -> list[ProductRecord]:
    records = []
    for row in _load("golden.json")["records"].values():
        variants = [
            VariantRow(**{**v, "price": Decimal(v["price"]) if v["price"] else None})
            for v in row["variants"]
        ]
        records.append(ProductRecord(**{**row, "variants": variants}))
    return records


def _synthetic(listing_id: int, product_type: str, search_text: str) -> ProductRecord:
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


async def _add_listing(
    conn: psycopg.AsyncConnection, listing_id: int, title: str, slug: str, active: bool = True
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, detail_hash, active)"
        " VALUES (%s, %s, %s, '{}', 'h', %s)",
        (listing_id, slug, title, active),
    )


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[psycopg.AsyncConnection]:
    run_migrations(database_url, REPO / "migrations")
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as c:
        listings = _load("listings.json")["listings"]
        for listing_id, body in listings.items():
            await _add_listing(c, int(listing_id), body["title"], body["slug"])
        await _add_listing(c, VEZNA_ZICA, "VEZNA ŽICA/POCINČANA 1,2MM", "vezna-zica-900001")
        await _add_listing(
            c, INACTIVE_PANEL, "PANELNA OGRADA PANEL", "panelna-ograda-900002", active=False
        )
        records = _golden_records()
        records.append(
            _synthetic(
                VEZNA_ZICA,
                "vezna žica",
                "VEZNA ŽICA/POCINČANA 1,2MM | vezna žica",
            )
        )
        records.append(
            _synthetic(
                INACTIVE_PANEL,
                "panelna ograda",
                "PANELNA OGRADA PANEL | panelna ograda | antracit",
            )
        )
        await save_products(c, records)
        yield c


def ids(hits: list[KeywordHit]) -> list[int]:
    return [hit.listing_id for hit in hits]


# --- matching and ranking --------------------------------------------------------------


async def test_finds_a_product_by_its_words(conn: psycopg.AsyncConnection) -> None:
    hits = await search_keyword(conn, "brusilica 125 mm")
    assert ids(hits)[0] == BRUSILICA


async def test_ranks_both_panel_listings_first(conn: psycopg.AsyncConnection) -> None:
    hits = await search_keyword(conn, "Treba mi panelna ograda za dvorište")
    assert set(ids(hits)[:2]) == PANELS


async def test_matches_inflected_forms(conn: psycopg.AsyncConnection) -> None:
    # "stubovi" is only a synonym; "stupova" is an inflection of "stup".
    assert T_POST in ids(await search_keyword(conn, "stubovi"))
    assert T_POST in ids(await search_keyword(conn, "metalnih stupova"))


async def test_a_typo_still_finds_the_product(conn: psycopg.AsyncConnection) -> None:
    # "brusilca" stems to nothing in the catalogue; trigram similarity finds it.
    assert BRUSILICA in ids(await search_keyword(conn, "brusilca"))


async def test_a_typo_in_a_multi_word_query_still_finds_the_product(
    conn: psycopg.AsyncConnection,
) -> None:
    # Trigram works per word: "brusilca" is close to "brusilica" even when the other
    # words ("za metal") are nowhere in the listing (R1-3).
    assert ids(await search_keyword(conn, "brusilca za metal"))[0] == BRUSILICA


async def test_typos_in_every_word_still_find_the_panels(conn: psycopg.AsyncConnection) -> None:
    # The typo seed (seed-08) without its colour word, so full text can't help at all.
    assert set(ids(await search_keyword(conn, "panelan ogarda"))[:2]) == PANELS
    assert set(ids(await search_keyword(conn, "panelan ogarda antracit"))[:2]) == PANELS


async def test_query_without_diacritics_matches_slash_joined_words(
    conn: psycopg.AsyncConnection,
) -> None:
    # The M0-2 review note: "žica/pocinčana" used to be one token, so the full-text half
    # (every word must match here: plainto_tsquery ANDs them) found nothing.
    cursor = await conn.execute(
        "SELECT listing_id FROM product"
        " WHERE search_tsv @@ plainto_tsquery('martom', martom_search_normalize(%s))",
        ("zica pocincana",),
    )
    assert VEZNA_ZICA in [row[0] for row in await cursor.fetchall()]
    assert VEZNA_ZICA in ids(await search_keyword(conn, "zica pocincana"))


async def test_a_size_glued_to_its_unit_matches_the_number(
    conn: psycopg.AsyncConnection,
) -> None:
    # The kokoši listing writes "H-100CM", "200CM"; a customer types "200 cm".
    assert KOKOSI in ids(await search_keyword(conn, "mreža 200 cm"))


async def test_never_returns_inactive_listings(conn: psycopg.AsyncConnection) -> None:
    hits = await search_keyword(conn, "panelna ograda panel antracit", limit=50)
    assert INACTIVE_PANEL not in ids(hits)
    assert set(ids(hits)) >= PANELS


async def test_limit_caps_the_results(conn: psycopg.AsyncConnection) -> None:
    assert len(await search_keyword(conn, "ograda", limit=2)) == 2


async def test_a_unit_glued_to_its_number_is_not_a_search_word(
    conn: psycopg.AsyncConnection,
) -> None:
    # "125mm" must count as "125", never as "125" | "mm": the padlock and the wall plugs
    # share only "mm" with this query (R1-2).
    glued = await search_keyword(conn, "brusilica 125mm", limit=50)
    spaced = await search_keyword(conn, "brusilica 125 mm", limit=50)
    assert ids(glued) == ids(spaced)
    assert KATANAC not in ids(glued)
    assert TIPLO not in ids(glued)


def test_strip_filler_words_splits_glued_units_and_drops_punctuation() -> None:
    assert strip_filler_words("Brusilica 125MM, 900W") == "Brusilica 125 900"
    assert strip_filler_words("ograda 1,5m") == "ograda 1,5"
    assert strip_filler_words("!!! ? panel ,") == "panel"
    assert strip_filler_words("treba mi ?") == ""


async def test_a_query_of_only_filler_words_returns_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await search_keyword(conn, "treba mi za") == []
    assert await search_keyword(conn, "   ") == []


async def test_a_hit_carries_its_listing_link(conn: psycopg.AsyncConnection) -> None:
    hit = (await search_keyword(conn, "T stup stub metalni"))[0]
    assert hit.listing_id == T_POST
    assert hit.product_type == "stup za ogradu"
    assert hit.url == (
        "https://martom.olx.ba/artikal/t-stup-stub-stupovi-metalni-35x35x350mm-h-100-125-150-175-200-59473597"
    )
    assert hit.score > 0


# --- filters ---------------------------------------------------------------------------


async def test_product_type_filter(conn: psycopg.AsyncConnection) -> None:
    hits = await search_keyword(conn, "ograda", SearchFilters(product_type="panelna ograda"))
    assert set(ids(hits)) == PANELS


async def test_height_filter_matches_heights_written_in_any_unit(
    conn: psycopg.AsyncConnection,
) -> None:
    # 150 cm +-5: "153" (cm), "1530 mm", "H-150 cm", "150CM", "H-150CM".
    hits = await search_keyword(conn, "ograda stup mreža", SearchFilters(height_cm=150), limit=50)
    assert set(ids(hits)) == PANELS | {T_POST, FARMER, KOKOSI}


async def test_colour_filter_reads_product_colours(conn: psycopg.AsyncConnection) -> None:
    hits = await search_keyword(conn, "ograda", SearchFilters(colour="antracit"), limit=50)
    assert set(ids(hits)) == PANELS


async def test_price_filter_needs_a_variant_in_range(conn: psycopg.AsyncConnection) -> None:
    filters = SearchFilters(max_price=Decimal("10"))
    hits = await search_keyword(conn, "katanac brusilica", filters, limit=50)
    # The padlock has a 6,75 KM variant; the grinder has no priced variant, so it can't
    # pass a price filter.
    assert ids(hits) == [KATANAC]


async def test_height_and_price_must_hold_for_the_same_variant(
    conn: psycopg.AsyncConnection,
) -> None:
    # Kokoši mesh: H-200CM costs 107,50 KM; the cheaper rolls are lower.
    cheap = SearchFilters(height_cm=200, max_price=Decimal("100"))
    assert KOKOSI not in ids(await search_keyword(conn, "mreža za kokoši", cheap))
    enough = SearchFilters(height_cm=200, max_price=Decimal("110"))
    assert KOKOSI in ids(await search_keyword(conn, "mreža za kokoši", enough))


async def test_min_price_filter(conn: psycopg.AsyncConnection) -> None:
    # Fischer wall plugs cost 0,15-0,75 KM; the padlock 6,75-18,00 KM.
    hits = await search_keyword(conn, "katanac fischer", SearchFilters(min_price=Decimal("1")))
    assert ids(hits) == [KATANAC]


# --- the SQL height parser -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cm"),
    [
        ("153", Decimal("153")),
        ("830 mm", Decimal("83")),
        ("H-100 cm", Decimal("100")),
        ("H-100CM", Decimal("100")),
        ("100CM", Decimal("100")),
        ("1,5 m", Decimal("150")),
        ("2 m", Decimal("200")),
        ("1,5 met", Decimal("150")),
        ("1,5 metara", Decimal("150")),
        ("1,5 metra", Decimal("150")),
        ("1.5m", Decimal("150")),
        ("1,5", Decimal("150")),  # no unit and below 10: metres
        ("20 METARA", Decimal("2000")),
        ("153 X 2500CM", Decimal("153")),
        ("H-", None),
        (None, None),
    ],
)
async def test_height_cm_parses_heights_as_written(
    conn: psycopg.AsyncConnection, raw: str | None, cm: Decimal | None
) -> None:
    cursor = await conn.execute("SELECT martom_height_cm(%s)", (raw,))
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == cm


# --- the eval runner -------------------------------------------------------------------


async def test_run_keyword_eval_returns_ranked_ids_for_every_question(
    conn: psycopg.AsyncConnection,
) -> None:
    questions = [
        EvalQuestion("brusilica", "Brusilica 125 mm za metal", (BRUSILICA,), "seed"),
        EvalQuestion("off", "Koje je radno vrijeme apoteke?", (), "seed"),
    ]
    results = await run_keyword_eval(conn, questions, limit=10)

    assert set(results) == {"brusilica", "off"}
    assert results["brusilica"][0] == BRUSILICA
    assert len(results["brusilica"]) <= 10
