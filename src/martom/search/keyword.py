"""Keyword search (M3-2): Postgres full-text + trigram over `product.search_text`, with filters.

The baseline that hybrid search (M3-3) must beat on recall@10.

How a query runs (all in one SQL statement):

1. Python drops filler words ("treba", "mi", "za", ...), so they can't decide a match.
2. Postgres normalises the rest with `martom_search_normalize` (migration 006: unaccent,
   split "žica/pocinčana", "H-100", "153CM") and stems it with the `martom` config.
3. The words are OR-ed, not AND-ed: a customer's sentence rarely has every word in one
   listing ("panelna ograda za dvorište" has no listing that says "dvorište"). Ranking
   then puts the listings that match more words, closer together, first.
4. Trigram similarity (pg_trgm `word_similarity`) adds typo tolerance: "brusilca" finds
   "brusilica" even though the stems differ.
5. Filters (type, height, colour, price) narrow the candidates; they never add any.

Only listings in `active_olx_listing` are returned, never sold ones (CLAUDE.md).

Search returns listing ids and names, never prices: prices in an answer come from the
database through the tools that read them (the grounding rule).
"""

import re
from dataclasses import dataclass
from decimal import Decimal

import psycopg

LISTING_URL = "https://martom.olx.ba/artikal/{slug}"

# Words that say nothing about the product. Compared after lower-casing; both the forms
# with and without diacritics are listed, because customers type both. Units are filler
# too: the number carries the meaning ("125 mm" -> "125"), while "mm" or "cm" appears in
# almost every listing many times and would decide the ranking on its own.
FILLER_WORDS = frozenset(
    {
        "a",
        "ako",
        "bi",
        "bih",
        "da",
        "do",
        "ga",
        "i",
        "ili",
        "ima",
        "imam",
        "imate",
        "ja",
        "je",
        "jedan",
        "jedna",
        "li",
        "me",
        "mi",
        "mo",
        "moze",
        "može",
        "na",
        "nam",
        "neki",
        "neka",
        "nesto",
        "nešto",
        "od",
        "o",
        "po",
        "sa",
        "se",
        "s",
        "su",
        "šta",
        "sta",
        "to",
        "treba",
        "trebam",
        "trebaju",
        "trebalo",
        "u",
        "uz",
        "za",
        "koliko",
        "kosta",
        "košta",
        "molim",
        "hvala",
        "pozdrav",
        "htio",
        "htjela",
        "zanima",
        "zanimaju",
        "mm",
        "cm",
        "m",
        "metara",
        "metra",
        "kom",
    }
)

# Weight of trigram similarity next to the full-text rank. Full-text decides most
# rankings; trigram mostly rescues typos, which full-text scores at 0.
TRIGRAM_WEIGHT = 0.3
# The height filter accepts heights this close to the one asked for: a customer's
# "1,5 m" fence should find the 153 cm panels.
HEIGHT_TOLERANCE_CM = Decimal(5)
# Trigram-only matches need at least this word similarity (0..1) to count at all.
MIN_TRIGRAM_SIMILARITY = 0.5

_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class SearchFilters:
    """Optional filters; None means "don't filter on this"."""

    product_type: str | None = None  # e.g. "panelna ograda"; all its words must match
    height_cm: float | None = None  # matched within HEIGHT_TOLERANCE_CM
    colour: str | None = None  # e.g. "antracit"; product or variant colours
    min_price: Decimal | None = None  # a variant's price, in KM
    max_price: Decimal | None = None


@dataclass(frozen=True)
class KeywordHit:
    listing_id: int
    title: str
    slug: str | None
    product_type: str
    score: float

    @property
    def url(self) -> str | None:
        return LISTING_URL.format(slug=self.slug) if self.slug else None


def strip_filler_words(query: str) -> str:
    """The query without filler words; punctuation-only words go too."""
    words = [w for w in _WORD.findall(query) if w.lower().strip(".,!?;:") not in FILLER_WORDS]
    return " ".join(words)


# %(...)s are psycopg parameters (values are sent separately, never pasted into the SQL).
# `query` turns the words into an OR tsquery: plainto_tsquery gives 'a' & 'b', and
# replacing the & with | gives 'a' | 'b' (lexemes are quoted, so that is safe).
# `<%` is pg_trgm's "word similarity above pg_trgm.word_similarity_threshold" operator;
# we compare the value ourselves instead so the threshold lives here, in code.
_SEARCH = """
WITH query AS (
    SELECT
        replace(
            plainto_tsquery('martom', martom_search_normalize(%(text)s))::text, ' & ', ' | '
        )::tsquery AS tsq,
        martom_search_normalize(%(text)s) AS norm
),
scored AS (
    SELECT
        p.listing_id,
        l.title,
        l.slug,
        p.product_type,
        ts_rank_cd(p.search_tsv, query.tsq) AS text_rank,
        word_similarity(query.norm, martom_search_normalize(p.search_text)) AS trigram,
        p.search_tsv @@ query.tsq AS text_match
    FROM product AS p
    JOIN active_olx_listing AS l ON l.id = p.listing_id
    CROSS JOIN query
    WHERE
        (%(product_type)s::text IS NULL
         OR to_tsvector('martom', martom_search_normalize(p.product_type))
            @@ plainto_tsquery('martom', martom_search_normalize(%(product_type)s::text)))
        AND (%(colour)s::text IS NULL
         OR to_tsvector('martom', martom_search_normalize(
                array_to_string(p.colours, ' ') || ' ' || coalesce(
                    (SELECT string_agg(v.colour, ' ') FROM product_variant AS v
                     WHERE v.listing_id = p.listing_id), '')))
            @@ plainto_tsquery('martom', martom_search_normalize(%(colour)s::text)))
        AND (
            (%(height_cm)s::numeric IS NULL
             AND %(min_price)s::numeric IS NULL AND %(max_price)s::numeric IS NULL)
            -- Height and price must hold for the same variant (a 200 cm roll at 107 KM
            -- does not pass "200 cm, under 100 KM" because the 100 cm roll costs 57 KM).
            OR EXISTS (
                SELECT 1 FROM product_variant AS v
                WHERE v.listing_id = p.listing_id
                  AND (%(height_cm)s::numeric IS NULL
                       OR abs(martom_height_cm(v.height) - %(height_cm)s::numeric)
                          <= %(tolerance)s::numeric)
                  AND (%(min_price)s::numeric IS NULL OR v.price >= %(min_price)s::numeric)
                  AND (%(max_price)s::numeric IS NULL OR v.price <= %(max_price)s::numeric)
            )
        )
)
SELECT listing_id, title, slug, product_type, text_rank + %(trigram_weight)s * trigram AS score
FROM scored
WHERE text_match OR trigram >= %(min_trigram)s
ORDER BY score DESC, listing_id
LIMIT %(limit)s
"""


async def search_keyword(
    conn: psycopg.AsyncConnection,
    query: str,
    filters: SearchFilters | None = None,
    *,
    limit: int = 10,
) -> list[KeywordHit]:
    """Active products matching `query` and `filters`, best first (at most `limit`)."""
    filters = filters or SearchFilters()
    text = strip_filler_words(query)
    if not text:
        return []
    cursor = await conn.execute(
        _SEARCH,
        {
            "text": text,
            "product_type": filters.product_type,
            "colour": filters.colour,
            "height_cm": filters.height_cm,
            "tolerance": HEIGHT_TOLERANCE_CM,
            "min_price": filters.min_price,
            "max_price": filters.max_price,
            "trigram_weight": TRIGRAM_WEIGHT,
            "min_trigram": MIN_TRIGRAM_SIMILARITY,
            "limit": limit,
        },
    )
    return [
        KeywordHit(
            listing_id=row[0],
            title=row[1] or "",
            slug=row[2],
            product_type=row[3],
            score=float(row[4]),
        )
        for row in await cursor.fetchall()
    ]
