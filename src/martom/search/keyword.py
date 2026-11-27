"""Keyword search (M3-2): Postgres full-text + trigram over `product.search_text`, with filters.

The baseline that hybrid search (M3-3) must beat on recall@10.

How a query runs (all in one SQL statement):

1. Python drops filler words ("treba", "mi", "za", ...) and units ("125mm" -> "125"), so
   they can't decide a match.
2. Postgres normalises the rest with `martom_search_normalize` (migration 006: unaccent,
   split "žica/pocinčana", "H-100", "153CM") and stems it with the `martom` config.
3. The words are OR-ed, not AND-ed: a customer's sentence rarely has every word in one
   listing ("panelna ograda za dvorište" has no listing that says "dvorište"). Ranking
   then puts the listings that match more words, closer together, first.
4. Trigram similarity (pg_trgm `word_similarity`, per query word) adds typo tolerance:
   "brusilca za metal" finds "brusilica" even though the stems differ. Both halves find
   their candidates through GIN indexes (migration 006); only candidates are ranked.
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
        "met",
        "metara",
        "metra",
        "kom",
        "kg",
        "w",
    }
)

# Weight of trigram similarity next to the full-text rank. Full-text decides most
# rankings; trigram mostly rescues typos, which full-text scores at 0.
TRIGRAM_WEIGHT = 0.3
# The height filter accepts heights this close to the one asked for: a customer's
# "1,5 m" fence should find the 153 cm panels.
HEIGHT_TOLERANCE_CM = Decimal(5)
# A listing that matches no word by full text still counts when one query word is more
# than this similar (pg_trgm word_similarity, 0..1) to some part of it: "brusilca" vs
# "brusilica" is 0.67, "panelan" vs "panelna" 0.625. 0.55, not 0.5: the off-topic
# "radno" is exactly 0.5 similar to the grinder's text. Only words of
# TRIGRAM_MIN_WORD_LENGTH characters or more that contain a letter can do this, so a
# number never makes a match on its own ("1000" is 0.8 similar to "100", "80" to "800").
MIN_TRIGRAM_SIMILARITY = 0.55
TRIGRAM_MIN_WORD_LENGTH = 4

_WORD = re.compile(r"\S+")
# A number with its unit glued on ("125MM", "1,5m", "900W"). The unit is split off so it is
# dropped as a filler word below; otherwise Postgres would split it later and search for
# "mm" on its own, which nearly every listing contains (review R1-2).
_GLUED_UNIT = re.compile(r"(\d)(mm|cm|metara|metra|met|m|kom|kg|w)\b", re.IGNORECASE)
_PUNCTUATION = ".,!?;:()\"'"


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
    """The query without filler words, units and punctuation-only words.

    "Brusilica 125MM, 900W" -> "Brusilica 125 900".
    """
    spaced = _GLUED_UNIT.sub(r"\1 \2", query)
    words = []
    for word in _WORD.findall(spaced):
        bare = word.lower().strip(_PUNCTUATION)
        if bare and bare not in FILLER_WORDS:
            words.append(word.strip(_PUNCTUATION))
    return " ".join(words)


# %(...)s are psycopg parameters (values are sent separately, never pasted into the SQL).
#
# `query` turns the words into an OR tsquery: plainto_tsquery gives 'a' & 'b', and
# replacing the & with | gives 'a' | 'b' (lexemes are quoted, so that is safe). It also
# keeps the normalised words for trigram matching, one word at a time (a whole multi-word
# query is never similar enough to a listing, review R1-3), and `typo_words`: the words
# allowed to make a match by trigram alone.
#
# `candidates` collects the matching listings: full text (`search_tsv @@ tsq`) and trigram
# (`typo_word <% search_norm`, written <%% because % starts a psycopg parameter). `<%` is
# true when the word similarity is at least pg_trgm.word_similarity_threshold, which
# search_keyword sets to MIN_TRIGRAM_SIMILARITY for this query. UNION drops duplicates.
# Both conditions can be answered from a GIN index (migration 006). On 2,600 products
# Postgres still chose a sequential scan for the trigram half (~50-100 ms per query): it
# prices the similarity check at almost nothing per row, so reading every row looks
# cheaper than the index. With sequential scans switched off the same query took
# ~6-23 ms. It may switch to the index on a bigger catalogue (not measured); forcing it
# is not worth it for the baseline.
#
# `scored` ranks only the candidates. Its LATERAL subquery runs once per candidate row
# (like a correlated subquery): `trigram` is the mean similarity of all query words, for
# ranking; `best_word` the best similarity of a typo word, checked again at the end so the
# threshold holds even if the setting differs.
_SEARCH = """
WITH query AS (
    SELECT
        replace(
            plainto_tsquery('martom', martom_search_normalize(%(text)s))::text, ' & ', ' | '
        )::tsquery AS tsq,
        words,
        ARRAY(
            SELECT word FROM unnest(words) AS word
            WHERE word ~ '[a-z]' AND length(word) >= %(min_len)s
        ) AS typo_words
    FROM (
        SELECT array_remove(
            regexp_split_to_array(martom_search_normalize(%(text)s), '[^a-z0-9]+'), ''
        ) AS words
    ) AS split
),
candidates AS (
    SELECT p.listing_id
    FROM product AS p CROSS JOIN query
    WHERE p.search_tsv @@ query.tsq
    UNION
    SELECT p.listing_id
    FROM query
    CROSS JOIN unnest(query.typo_words) AS typo(word)
    JOIN product AS p ON typo.word <%% p.search_norm
),
scored AS (
    SELECT
        p.listing_id,
        l.title,
        l.slug,
        p.product_type,
        ts_rank_cd(p.search_tsv, query.tsq) AS text_rank,
        similarity.trigram,
        similarity.best_word,
        p.search_tsv @@ query.tsq AS text_match
    FROM candidates AS c
    JOIN product AS p ON p.listing_id = c.listing_id
    JOIN active_olx_listing AS l ON l.id = p.listing_id
    CROSS JOIN query
    CROSS JOIN LATERAL (
        SELECT
            coalesce(avg(word_similarity(word, p.search_norm)), 0) AS trigram,
            coalesce(
                (SELECT max(word_similarity(typo_word, p.search_norm))
                 FROM unnest(query.typo_words) AS typo_word), 0
            ) AS best_word
        FROM unnest(query.words) AS word
    ) AS similarity
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
                  AND (
                      (%(min_price)s::numeric IS NULL AND %(max_price)s::numeric IS NULL)
                      -- A price filter needs a real price: a 0 KM variant never
                      -- matches (R3-1). A variant price comes from the description, so
                      -- it counts even when the listing itself is "Na upit".
                      OR (v.price > 0
                          AND (%(min_price)s::numeric IS NULL
                               OR v.price >= %(min_price)s::numeric)
                          AND (%(max_price)s::numeric IS NULL
                               OR v.price <= %(max_price)s::numeric))
                  )
            )
            -- A listing that sells one thing has no variants; its price is on the listing
            -- (R2-1). A height filter can't match it: there is no height to read. A
            -- listing price of 0 (how OLX stores "Na upit") or a listing sold by
            -- agreement is not a real price, so it never matches (R3-1).
            OR (
                %(height_cm)s::numeric IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM product_variant AS v WHERE v.listing_id = p.listing_id
                )
                AND l.price > 0
                AND NOT coalesce(l.price_by_agreement, false)
                AND (%(min_price)s::numeric IS NULL OR l.price >= %(min_price)s::numeric)
                AND (%(max_price)s::numeric IS NULL OR l.price <= %(max_price)s::numeric)
            )
        )
)
SELECT listing_id, title, slug, product_type, text_rank + %(trigram_weight)s * trigram AS score
FROM scored
WHERE text_match OR best_word >= %(min_trigram)s
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
    # The typo threshold for `<%` is a setting, so it is set for this query only: in a
    # transaction (a savepoint, if the caller already has one open), with is_local=true,
    # which Postgres undoes when that transaction ends. Inside a caller's own transaction
    # it lasts until that transaction ends.
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)",
            (str(MIN_TRIGRAM_SIMILARITY),),
        )
        rows = await _run_search(conn, text, filters, limit)
    return [
        KeywordHit(
            listing_id=row[0],
            title=row[1] or "",
            slug=row[2],
            product_type=row[3],
            score=float(row[4]),
        )
        for row in rows
    ]


async def _run_search(
    conn: psycopg.AsyncConnection, text: str, filters: SearchFilters, limit: int
) -> list[tuple]:
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
            "min_len": TRIGRAM_MIN_WORD_LENGTH,
            "limit": limit,
        },
    )
    return await cursor.fetchall()
