"""Category sync (M1-2): find every listing of the shop on OLX, once, and say how sure we are.

Why not just page through `/users/{shop}/listings`? Because OLX clamps the page number:
any page above 50 silently returns page 50 again (`meta.current_page` stays 50). At 20 per
page, 129 pages gave 2,585 rows but only 996 unique listings (recorded in
`tests/fixtures/olx/listings_pagination.json`). Consecutive pages can also overlap a little.

So the sync works in small pieces that each fit under the clamp:

1. `discover`: the shop's id and reported total, plus its categories with counts (from the
   `/search` aggregation, which lists at most 20 sub-categories per top category).
2. `plan_targets`: page every listed sub-category ("leaf"); page the whole top category as
   well when its listed sub-categories don't add up to its count (the cap hid some); and
   finish with one whole-shop sweep to catch listings in no reported category.
3. `page_target`: page one target to its last page, but never past page 50
   (`OLX_MAX_PAGE`). It stops early, and says why, at page 50 or when OLX clamps or
   repeats a page.
4. `sync_shop`: run all targets, dedupe by listing id, recount the shop total at the end,
   and return a `SyncResult` with the coverage.

Deactivation rule (CLAUDE.md): listings may only be deactivated when the sync saw at least
as many unique listings as the shop reports. `deactivation_candidates` is the one place that
rule lives. It returns candidates, not verdicts: M1-4 confirms each one with
GET /listings/{id} before deactivating it (see that function's docstring for why).

Detail fetch and database writes are out of scope here (M1-3, M1-4).
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self

from martom.olx.client import OlxApiError, OlxClient

logger = logging.getLogger(__name__)

# OLX accepts per_page=100 (checked 26 Nov 2026): fewer requests, and 50 pages of 100
# reach 5,000 listings per query before the clamp.
DEFAULT_PER_PAGE = 100
# OLX serves page 50 again for any page above 50 (recorded 26 Nov 2026), so never ask.
OLX_MAX_PAGE = 50

TargetKind = Literal["leaf", "top", "shop"]
StopReason = Literal["last_page", "empty_page", "page_clamped", "repeated_page"]


class OlxResponseError(OlxApiError):
    """OLX answered 200, but the JSON is missing a field the sync needs (or has the wrong type).

    A subclass of `OlxApiError`, so one `except OlxApiError` in M1-4 covers it too.
    """


def _unexpected(what: str, exc: Exception) -> OlxResponseError:
    error = OlxResponseError(f"unexpected OLX response: {what} ({exc!r})")
    error.__cause__ = exc
    return error


# --- Discovery --------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    """One category as the `/search` aggregation reports it."""

    id: int
    name: str
    count: int


@dataclass(frozen=True)
class TopCategory(Category):
    sub_categories: tuple[Category, ...] = ()

    @property
    def sub_categories_complete(self) -> bool:
        """True when the listed sub-categories account for every listing in this one."""
        return sum(sub.count for sub in self.sub_categories) >= self.count


@dataclass(frozen=True)
class Discovery:
    shop_id: int
    shop_total: int
    top_categories: tuple[TopCategory, ...]

    # `@classmethod` is like a static factory in TS: `Discovery.from_api(...)`; `cls` is
    # the class itself.
    @classmethod
    def from_api(
        cls, *, shop_id: int, shop_total: int, categories: Sequence[Mapping[str, Any]]
    ) -> Self:
        """Build from the raw `aggregations.categories` list of a `/search` response."""
        tops = tuple(
            TopCategory(
                id=int(top["id"]),
                name=str(top["name"]),
                count=int(top["count"]),
                sub_categories=tuple(
                    Category(id=int(sub["id"]), name=str(sub["name"]), count=int(sub["count"]))
                    for sub in top.get("sub_categories") or ()
                ),
            )
            for top in categories
        )
        return cls(shop_id=shop_id, shop_total=shop_total, top_categories=tops)


async def discover(olx: OlxClient) -> Discovery:
    """Ask OLX for the shop's id, its reported listing total and its category tree.

    Three requests: the shop profile (for the numeric id `/search` needs), page 1 of the
    shop's listings (its `meta.total` is the number we reconcile against) and the search
    aggregation.
    """
    profile = await olx.get_shop()
    try:
        shop_id = int(profile["data"]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _unexpected("shop profile has no data.id", exc) from exc
    shop_total = await _shop_total(olx)
    search = await olx.search_listings({"user_id": shop_id, "per_page": 1})
    try:
        categories = (search.get("aggregations") or {}).get("categories") or []
        return Discovery.from_api(shop_id=shop_id, shop_total=shop_total, categories=categories)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise _unexpected("search aggregation is malformed", exc) from exc


async def _shop_total(olx: OlxClient) -> int:
    body = await olx.get_shop_listings({"page": 1, "per_page": 1})
    try:
        return int(body["meta"]["total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _unexpected("shop listings have no meta.total", exc) from exc


# --- Planning ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageTarget:
    """One query to page through: a leaf category, a top category, or the whole shop."""

    kind: TargetKind
    category_id: int | None
    name: str

    @classmethod
    def leaf(cls, category_id: int, name: str) -> Self:
        return cls(kind="leaf", category_id=category_id, name=name)

    @classmethod
    def top(cls, category_id: int, name: str) -> Self:
        return cls(kind="top", category_id=category_id, name=name)

    @classmethod
    def whole_shop(cls) -> Self:
        return cls(kind="shop", category_id=None, name="whole shop")

    def matches(self, row: Mapping[str, Any]) -> bool:
        """Does this row belong to the target? Pinned rows from elsewhere don't count."""
        if self.kind == "leaf":
            return _as_int(row.get("category_id")) == self.category_id
        if self.kind == "top":
            # OLX sends top_category_id as a string ("701") and category_id as a number.
            return _as_int(row.get("top_category_id")) == self.category_id
        return True


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def plan_targets(discovery: Discovery) -> list[PageTarget]:
    """Leaves first, then tops whose leaves don't add up, then one whole-shop sweep.

    Tops are taken in id order so a run is reproducible; leaves in the order OLX lists them.
    """
    targets: list[PageTarget] = []
    for top in sorted(discovery.top_categories, key=lambda t: t.id):
        targets.extend(PageTarget.leaf(sub.id, sub.name) for sub in top.sub_categories)
        if not top.sub_categories_complete:
            targets.append(PageTarget.top(top.id, top.name))
    targets.append(PageTarget.whole_shop())
    return targets


# --- Paging one target ------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCoverage:
    """What paging one target returned, compared to the total OLX reported for it."""

    target: PageTarget
    reported_total: int  # `meta.total` of the target's first page
    rows: int  # every row received, duplicates and pinned rows included
    unique_matching: int  # unique ids among rows that belong to the target
    pages_fetched: int
    stop_reason: StopReason

    @property
    def complete(self) -> bool:
        """Advisory only: the deactivation gate uses the overall unique count, not this.

        A target can miss a listing or two on its own and still be covered by another
        target. The live run on 26 Nov showed leaf 1126 at 492 of 493 and top 224 at 1,552
        of 1,557, probably because the list shifts between page requests or a listing sits
        in a deeper sub-category than the one being matched. The other targets still
        brought the run to 100%.
        """
        return self.stop_reason == "last_page" and self.unique_matching >= self.reported_total


async def page_target(
    olx: OlxClient, target: PageTarget, *, per_page: int = DEFAULT_PER_PAGE
) -> tuple[TargetCoverage, list[dict[str, Any]]]:
    """Page `target` from page 1 to its last page; return its coverage and every row.

    Never requests a page above `OLX_MAX_PAGE`: a target with more pages stops after page
    50 as "page_clamped". It also stops early, without raising, when OLX answers a different
    page than asked ("page_clamped"), sends the same ids as the previous page
    ("repeated_page") or an empty page. In all those cases the coverage says the target is
    incomplete. A response missing `id` or `meta` fields raises `OlxResponseError`.
    """
    rows: list[dict[str, Any]] = []
    matching_ids: set[int] = set()
    previous_ids: list[int] | None = None
    reported_total = 0
    last_page = 1
    page = 0
    stop_reason: StopReason = "page_clamped"  # what's left if we run out of allowed pages

    while page < OLX_MAX_PAGE:
        page += 1
        params: dict[str, str | int] = {"page": page, "per_page": per_page}
        if target.category_id is not None:
            params["category_id"] = target.category_id
        body = await olx.get_shop_listings(params)
        try:
            meta = body.get("meta") or {}
            data: list[dict[str, Any]] = body.get("data") or []
            if page == 1:
                reported_total = int(meta["total"])
                last_page = int(meta.get("last_page", 1))
            page_ids = [int(row["id"]) for row in data]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise _unexpected(f"listings page {page} of {target.name} is malformed", exc) from exc

        current_page = _as_int(meta.get("current_page"))
        if current_page is not None and current_page != page:
            stop_reason = "page_clamped"
            break
        if page_ids and page_ids == previous_ids:
            stop_reason = "repeated_page"
            break
        if not data:
            stop_reason = "last_page" if page >= last_page else "empty_page"
            break

        rows.extend(data)
        matching_ids.update(int(row["id"]) for row in data if target.matches(row))
        # When last_page > OLX_MAX_PAGE the loop ends after page 50 with "page_clamped".
        previous_ids = page_ids
        if page >= last_page:
            stop_reason = "last_page"
            break

    coverage = TargetCoverage(
        target=target,
        reported_total=reported_total,
        rows=len(rows),
        unique_matching=len(matching_ids),
        pages_fetched=page,
        stop_reason=stop_reason,
    )
    if not coverage.complete:
        logger.info(
            "%s %s: %d of %d listings, stopped: %s",
            target.kind,
            target.category_id,
            coverage.unique_matching,
            coverage.reported_total,
            stop_reason,
        )
    return coverage, rows


# --- The whole sync ---------------------------------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    """Every unique listing summary seen, and how that compares to the shop's total."""

    shop_total_at_start: int
    shop_total_at_end: int
    listings: Mapping[int, Mapping[str, Any]]  # listing id -> first summary row seen
    targets: tuple[TargetCoverage, ...]

    @property
    def shop_total(self) -> int:
        """The total to reconcile against: the larger of the two counts, to be safe."""
        return max(self.shop_total_at_start, self.shop_total_at_end)

    @property
    def unique_count(self) -> int:
        return len(self.listings)

    @property
    def coverage(self) -> float:
        """Unique listings seen / shop total, 0.0 for an empty shop."""
        if self.shop_total <= 0:
            return 0.0
        return self.unique_count / self.shop_total

    @property
    def is_complete(self) -> bool:
        """True only when we saw at least as many unique listings as OLX reports.

        An empty shop is never complete: a 0 from OLX is more likely a glitch than a shop
        that sold everything, and "complete" would let M1-4 deactivate every listing.
        """
        return self.shop_total > 0 and self.unique_count >= self.shop_total


async def sync_shop(olx: OlxClient, *, per_page: int = DEFAULT_PER_PAGE) -> SyncResult:
    """Discover, page every target, dedupe by listing id and reconcile with the shop total.

    `OlxApiError` from the client propagates: a failed request means this run can't vouch
    for its coverage, and M1-4 must not deactivate anything after it.
    """
    discovery = await discover(olx)
    targets = plan_targets(discovery)
    logger.info(
        "shop %d reports %d listings in %d top categories; paging %d targets",
        discovery.shop_id,
        discovery.shop_total,
        len(discovery.top_categories),
        len(targets),
    )

    listings: dict[int, dict[str, Any]] = {}
    coverages: list[TargetCoverage] = []
    foreign_ids: set[int] = set()
    for target in targets:
        coverage, rows = await page_target(olx, target, per_page=per_page)
        coverages.append(coverage)
        for row in rows:
            listing_id = int(row["id"])
            # A row from another user (say, a promoted listing) must not count as ours.
            owner = _as_int(row.get("user_id"))
            if owner is not None and owner != discovery.shop_id:
                foreign_ids.add(listing_id)
                continue
            # setdefault keeps the first row seen for an id (like `??=` on a JS object key).
            listings.setdefault(listing_id, row)
    if foreign_ids:
        logger.warning(
            "ignored %d listings that belong to another user: %s",
            len(foreign_ids),
            sorted(foreign_ids)[:20],
        )

    result = SyncResult(
        shop_total_at_start=discovery.shop_total,
        shop_total_at_end=await _shop_total(olx),
        listings=listings,
        targets=tuple(coverages),
    )
    log = logger.info if result.is_complete else logger.warning
    log(
        "sync saw %d unique listings of %d reported (%.2f%%); %s",
        result.unique_count,
        result.shop_total,
        result.coverage * 100,
        "complete" if result.is_complete else "INCOMPLETE: nothing may be deactivated",
    )
    return result


def deactivation_candidates(active_ids: Iterable[int], result: SyncResult) -> set[int]:
    """Listing ids active in our database that this sync did not see: CANDIDATES only.

    Returns an empty set unless the sync covered 100% of the shop's reported total, so a
    partial run never even proposes a listing.

    Why only candidates: the 100% gate compares counts, not sets. During a ~3-minute run a
    listing X can be seen and then sold while a new listing Y is added, so the start and
    end totals stay equal. If that same change shifts a page so that active listing Z is
    missed by every target, the run still counts as complete and Z shows up here, though
    it is still for sale. So M1-4 must confirm each candidate with GET /listings/{id}
    (through the rate-limited client) and deactivate it only when OLX says it is gone or
    inactive.
    """
    if not result.is_complete:
        return set()
    return set(active_ids) - set(result.listings)


__all__ = [
    "DEFAULT_PER_PAGE",
    "OLX_MAX_PAGE",
    "Category",
    "Discovery",
    "OlxApiError",
    "OlxResponseError",
    "PageTarget",
    "SyncResult",
    "TargetCoverage",
    "TopCategory",
    "deactivation_candidates",
    "discover",
    "page_target",
    "plan_targets",
    "sync_shop",
]
