"""Detail fetch (M1-3): clean listing descriptions and decide what to fetch again.

Three pieces, used by the nightly sync (M1-4):

1. `clean_description`: description HTML -> plain text, with the shop's footer and other
   boilerplate (address, phone numbers, e-mail, PIKA and social-media lines, "see more
   HERE" links, the list of reference customers) removed. Everything else is copied as
   written, odd sizes and LaTeX included: extraction (M2-1) reads the facts, and the
   deterministic checks flag problems for Toni. The footer is removed line by line, not
   by cutting the text at the first footer line, because some old descriptions put the
   footer in the middle with panel sizes after it (listing 1199211 in the fixtures).
2. Two hashes for change detection (docs/PLAN.md, "Change detection"):
   - `summary_hash(row)`: over the listing-page row. If it hasn't changed since the last
     run, the listing is not fetched again (`needs_detail_fetch`).
   - `ListingDetail.detail_hash`: over the cleaned detail. M2-1 re-extracts a listing only
     when it changes, so a footer edit on every listing triggers nothing.
3. `fetch_changed_details`: GET /listings/{id} (through the rate-limited client) for every
   listing whose summary hash is new or changed.

Known gap: the summary row doesn't carry the description, so an edit that changes only
the description (not the title, price, images, ...) is not noticed until something in the
summary changes too. The OLX `date` field can't help: OLX bumps it whenever the shop
refreshes a listing (every recorded row was dated the day it was recorded), so hashing it
would re-fetch the whole shop every night. The weekly `martom sync --full` (M1-4)
re-fetches every detail to close that gap.
"""

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Self

from martom.olx.client import OlxApiError, OlxClient, OlxRateLimitAbortError
from martom.olx.sync import OlxResponseError

logger = logging.getLogger(__name__)

LISTING_URL = "https://martom.olx.ba/artikal/{slug}"

# --- HTML to text -------------------------------------------------------------------------

# Tags that start a new line. Inline tags (strong, em, a, span, b) add nothing, so
# "kvalitetene<a> trake</a>" stays "kvalitetene trake".
_BLOCK_TAGS = frozenset(
    {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
     "caption", "dl", "dt", "dd", "pre", "blockquote", "section", "article", "header",
     "footer", "hr"}
)  # fmt: skip
# Table cells on one row are joined with " | ", so "153 x 250" and "45,00 KM" in two cells
# can't run together into "153 x 25045,00 KM".
_CELL_TAGS = frozenset({"td", "th"})
_CELL_SEPARATOR = " | "
_SKIP_TAGS = frozenset({"script", "style"})
_INVISIBLE = str.maketrans({"\xa0": " ", "\ufeff": None, "\u200b": None})
_SPACES = re.compile(r"[^\S\n]+")  # any whitespace except a newline


class _TextCollector(HTMLParser):
    """Collects the text of an HTML fragment, one line per block element.

    `HTMLParser` calls the `handle_*` methods as it reads the HTML (like SAX events).
    With `convert_charrefs=True` (the default) entities such as `&amp;` arrive decoded.
    """

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0  # > 0 while inside <script> or <style>
        self._table_depth = 0  # > 0 while inside a <table>
        self._line_has_text = False  # the current line has visible text so far

    def _append_break(self) -> None:
        self.parts.append("\n")
        self._line_has_text = False

    def _newline(self) -> None:
        # One boundary between blocks, however many tags close and open there.
        if self.parts and not self.parts[-1].endswith("\n"):
            self._append_break()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self._append_break()  # every <br> counts: <p><br></p> is a blank line
        elif tag in _CELL_TAGS:
            if self._line_has_text:
                self.parts.append(_CELL_SEPARATOR)
        elif tag in _BLOCK_TAGS:
            if tag == "table":
                self._table_depth += 1
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            if tag == "table":
                self._table_depth = max(0, self._table_depth - 1)
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._table_depth:
            # Source line breaks between cells are layout, not text: rows come from <tr>.
            data = data.replace("\n", " ")
        self.parts.append(data)
        if "\n" in data:
            self._line_has_text = bool(data.rsplit("\n", 1)[1].strip())
        elif data.strip():
            self._line_has_text = True


def description_to_text(html: str | None) -> str:
    """Plain text of a description: one line per paragraph, list item or <br>.

    Spaces inside a line are collapsed, lines are stripped, and runs of blank lines become
    one blank line. Nothing else changes: the words are copied exactly.
    """
    if not html:
        return ""
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    text = "".join(collector.parts).translate(_INVISIBLE)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _join_lines(lines)


def _join_lines(lines: list[str]) -> str:
    """Join lines, keeping at most one blank line in a row and none at either end."""
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip("\n")


# --- Boilerplate --------------------------------------------------------------------------

# A line matching any of these is shop boilerplate, unless it also carries a product fact
# (see `_FACT`). All are case-insensitive. The patterns come from the recorded descriptions
# in tests/fixtures/olx/listing_details.json; add a pattern and a fixture when a new footer
# style shows up.
_BOILERPLATE_LINE = re.compile(
    "|".join(
        [
            r"\b036[ /-]?651[ -]?427\b",  # the shop's public phone number
            r"martom-bekaert\.com",  # the shop's e-mail and website
            r"https?://|www\.",  # a bare link
            r"dostupno u želje?zarij",  # "Dostupno u Željezariji MARTOM ..."
            r"tromeđa\s+bb",  # the address
            r"praktična lokacija za kupce",
            r"osobno preuzimanje",
            r"dostupni odmah u našoj poslovnici",
            r"naručite i preko pika",
            r"šaljemo brzom poštom",
            r"zapratite nas",
            r"facebook\s*\|\s*instagram",
            r"pogledaj kompletnu ponudu",
            r"(pogleda|potraž)\w*\b.*\bovdje\b",  # "pogledati / potražite ... ovdje" links
            r"^\W*dostava:\s*$",  # a "📦 Dostava:" heading left on its own
        ]
    ),
    re.IGNORECASE,
)
# Any other phone number (Toni's mobile, say) counts only next to a contact word: three
# zero-led groups alone can be an article code or sizes ("Šifra artikla 063 120 150").
_PHONE = re.compile(r"\b0[3-6]\d[ /-]?\d{3}[ -]?\d{3,4}\b")
_CONTACT_CUE = re.compile(
    r"\btel\b|telefon|\bfax\b|kontakt|pozov|nazov|\bmob\b|📞|☎|\btoni\b", re.IGNORECASE
)
# A product fact: a number with a price or measuring unit ("18,00KM", "155 cm", "50 m",
# "4 kom") or a size ("83x200", "153 x 250"). A line with one is never dropped, whatever
# else it says: losing a variant is worse than keeping a phone number or a link.
_FACT = re.compile(
    r"\d(?:[\d.,]*\d)?\s*(?:km|kn|eur|€|mm|cm|m|m2|m²|kg|g|kom|komada|kpl|rol[aeiu]?|l|lit)"
    r"(?!\w)|\d\s*[x\u00d7]\s*\d",  # x or the multiplication sign
    re.IGNORECASE,
)
# The list of reference customers starts here. Its lines are dropped up to the next line
# that carries a fact (old descriptions put product facts after it, too).
_REFERENCES_HEADING = re.compile(r"^\W*naše reference\b", re.IGNORECASE)


def _is_boilerplate(line: str) -> bool:
    if _FACT.search(line):
        return False
    if _BOILERPLATE_LINE.search(line):
        return True
    return bool(_PHONE.search(line) and _CONTACT_CUE.search(line))


def strip_boilerplate(text: str) -> str:
    """Drop the shop's boilerplate lines from description text; keep every other line.

    Lines are kept or dropped whole, never edited, so what stays is copied as written.
    """
    kept: list[str] = []
    in_references = False
    for line in text.split("\n"):
        has_fact = bool(_FACT.search(line))
        if _REFERENCES_HEADING.search(line) and not has_fact:
            in_references = True
            continue
        if in_references:
            if not has_fact:
                continue
            in_references = False
        if _is_boilerplate(line):
            continue
        kept.append(line)
    return _join_lines(kept)


def clean_description(html: str | None) -> str:
    """Description HTML -> plain text without the shop footer (what M2-1 extracts from)."""
    return strip_boilerplate(description_to_text(html))


# --- Hashes -------------------------------------------------------------------------------

# Fields of a listing-page row that describe the listing itself. Everything else (`date`,
# `score`, `sponsored`, badges, `refresh_available`, ...) changes without the listing
# changing; an allowlist also keeps a new OLX field from re-fetching the whole shop.
SUMMARY_HASH_FIELDS = (
    "id", "title", "slug", "category_id", "top_category_id", "brand_id", "city_id",
    "user_id", "price", "display_price", "discounted_price", "discounted_price_float",
    "original_price", "has_discount", "show_price", "price_max", "images", "available",
    "visible", "state", "status", "listing_type", "shipping", "special_labels",
)  # fmt: skip


def _sha256_of(value: object) -> str:
    """sha256 hex of a canonical JSON form: same data, same hash, whatever the key order."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summary_hash(row: Mapping[str, Any]) -> str:
    """Hash of the listing-page row's content fields (`SUMMARY_HASH_FIELDS`)."""
    return _sha256_of({name: row.get(name) for name in SUMMARY_HASH_FIELDS})


def needs_detail_fetch(row: Mapping[str, Any], stored_summary_hash: str | None) -> bool:
    """True for a listing we have never fetched or whose summary changed since we did."""
    return stored_summary_hash is None or summary_hash(row) != stored_summary_hash


# --- One listing's details ----------------------------------------------------------------


def _typed[T](body: Mapping[str, Any], key: str, kind: type[T]) -> T | None:
    """`body[key]` if it is a `kind`, None if it is missing or null; else TypeError.

    `def _typed[T]` is Python 3.12 generic syntax, like `function typed<T>(...)` in TS.
    """
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, kind):
        raise TypeError(f"{key} is {type(value).__name__}, expected {kind.__name__}")
    return value


def _price(value: object) -> float | None:
    """A price is a number or null. `bool` is a subclass of `int` in Python, so exclude it."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"price is {type(value).__name__}, expected a number")
    return value


@dataclass(frozen=True)
class ListingDetail:
    """The parts of GET /listings/{id} we keep, plus the cleaned description."""

    id: int
    slug: str
    title: str
    short_description: str | None
    description_html: str  # as OLX sent it, for olx_listing (M1-4)
    description_text: str  # cleaned, what extraction (M2-1) reads
    price: float | None
    display_price: str | None
    price_by_agreement: bool
    state: str | None
    status: str | None
    available: bool | None
    category_id: int | None
    brand: str | None
    attributes: tuple[tuple[str, str], ...]  # (name, value), sorted

    @classmethod
    def from_api(cls, body: Mapping[str, Any]) -> Self:
        """Build from a GET /listings/{id} body; raise `OlxResponseError` if it's malformed."""
        try:
            listing_id = int(body["id"])
            title = body["title"]
            if not isinstance(title, str):
                raise TypeError(f"title is {type(title).__name__}")
            slug = _typed(body, "slug", str)
            if not slug:
                raise ValueError("no slug: the listing link needs it")
            additional = body.get("additional") or {}
            html = additional.get("description") or ""
            brand = body.get("brand")
            attributes = tuple(
                sorted(
                    (str(a["name"]), str(a["value"]))
                    for a in body.get("attributes") or ()
                    if a.get("value") is not None  # a null value is no value, not "None"
                )
            )
            category_id = body.get("category_id")
            return cls(
                id=listing_id,
                slug=slug,
                title=title,
                short_description=_typed(body, "short_description", str),
                description_html=html,
                description_text=clean_description(html),
                price=_price(body.get("price")),
                display_price=_typed(body, "display_price", str),
                price_by_agreement=bool(body.get("price_by_agreement")),
                state=_typed(body, "state", str),
                status=_typed(body, "status", str),
                available=_typed(body, "available", bool),
                category_id=int(category_id) if category_id is not None else None,
                brand=str(brand["name"]) if isinstance(brand, Mapping) else None,
                attributes=attributes,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            error = OlxResponseError(f"unexpected OLX response: listing detail ({exc!r})")
            raise error from exc

    @property
    def url(self) -> str:
        """Public link; `olx.ba/artikal/{id}` does not open, the slug form does."""
        return LISTING_URL.format(slug=self.slug)

    @property
    def detail_hash(self) -> str:
        """Hash of what extraction reads. Footer, views, stock and dates don't count."""
        return _sha256_of(
            {
                "title": self.title,
                "short_description": self.short_description,
                "description_text": self.description_text,
                "price": self.price,
                "display_price": self.display_price,
                "price_by_agreement": self.price_by_agreement,
                "state": self.state,
                "category_id": self.category_id,
                "brand": self.brand,
                "attributes": self.attributes,
            }
        )


# --- Fetching -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchedDetail:
    detail: ListingDetail
    summary_hash: str  # store it with the detail; next run compares against it


@dataclass(frozen=True)
class DetailFetchResult:
    fetched: dict[int, FetchedDetail] = field(default_factory=dict)
    unchanged: tuple[int, ...] = ()  # summary hash matched: not requested
    # Failed listings keep their old stored hash, so the next run tries them again.
    failed: dict[int, OlxApiError] = field(default_factory=dict)


class DetailFetchAbortedError(OlxRateLimitAbortError):
    """OLX stopped the run mid-fetch; `partial` holds everything fetched before that.

    A subclass of `OlxRateLimitAbortError`, so an `except OlxRateLimitAbortError` still
    catches it. M1-4 should save `partial.fetched` before ending the run, so the next run
    does not request those listings again (their stored summary hash now matches).
    """

    def __init__(self, message: str, *, partial: DetailFetchResult) -> None:
        super().__init__(message, status_code=None)
        self.partial = partial


async def fetch_changed_details(
    olx: OlxClient,
    rows: Mapping[int, Mapping[str, Any]],
    stored_summary_hashes: Mapping[int, str],
) -> DetailFetchResult:
    """Fetch details for listings that are new or whose summary hash changed.

    `rows` is listing id -> listing-page row (`SyncResult.listings` from M1-2);
    `stored_summary_hashes` is listing id -> the summary hash saved at the last fetch.

    One failed listing (a 404 because it sold meanwhile, a malformed body) is recorded in
    `failed` and the rest continue. When OLX stops the run (`OlxRateLimitAbortError`, see
    its docstring), this raises `DetailFetchAbortedError`, a subclass of it, carrying what
    was fetched before the abort in `.partial`, so M1-4 can save it before ending the run.
    """
    fetched: dict[int, FetchedDetail] = {}
    unchanged: list[int] = []
    failed: dict[int, OlxApiError] = {}
    for listing_id, row in rows.items():
        new_hash = summary_hash(row)
        if new_hash == stored_summary_hashes.get(listing_id):
            unchanged.append(listing_id)
            continue
        try:
            detail = ListingDetail.from_api(await olx.get_listing(listing_id))
            if detail.id != listing_id:
                raise OlxResponseError(
                    f"unexpected OLX response: asked for listing {listing_id}, got {detail.id}"
                )
        except OlxRateLimitAbortError as exc:
            partial = DetailFetchResult(fetched=fetched, unchanged=tuple(unchanged), failed=failed)
            logger.error(
                "detail fetch aborted at listing %d: %d fetched before the abort",
                listing_id,
                len(fetched),
            )
            raise DetailFetchAbortedError(str(exc), partial=partial) from exc
        except OlxApiError as exc:
            logger.warning("listing %d: detail fetch failed: %s", listing_id, exc)
            failed[listing_id] = exc
            continue
        fetched[listing_id] = FetchedDetail(detail=detail, summary_hash=new_hash)

    logger.info(
        "detail fetch: %d fetched, %d unchanged, %d failed",
        len(fetched),
        len(unchanged),
        len(failed),
    )
    return DetailFetchResult(fetched=fetched, unchanged=tuple(unchanged), failed=failed)


__all__ = [
    "SUMMARY_HASH_FIELDS",
    "DetailFetchAbortedError",
    "DetailFetchResult",
    "FetchedDetail",
    "ListingDetail",
    "clean_description",
    "description_to_text",
    "fetch_changed_details",
    "needs_detail_fetch",
    "strip_boilerplate",
    "summary_hash",
]
