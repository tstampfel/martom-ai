"""Tests for the category sync (M1-2): discovery, per-category paging, dedupe, coverage.

Two kinds of test data:

- `tests/fixtures/olx/listings_pagination.json`: real OLX responses recorded on 26 Nov 2026
  and trimmed to a few fields. They show the duplicate-pagination bug: past page 50 OLX
  silently serves page 50 again (with `meta.current_page` still 50), so 129 pages gave
  2,585 rows but only 996 unique listings.
- `FakeOlx`: a small fake OLX server built on `httpx.MockTransport` that behaves the way
  the recording shows (page clamping, pinned rows on page 1, sub-category lists capped in
  the search aggregation), on a shop small enough to read in a test.
"""

import asyncio
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

from martom.olx.client import OlxClient
from martom.olx.sync import (
    Discovery,
    PageTarget,
    SyncResult,
    TargetCoverage,
    discover,
    ids_to_deactivate,
    page_target,
    plan_targets,
    sync_shop,
)

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).parent / "fixtures" / "olx" / "listings_pagination.json"
SHOP_ID = 17193


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


class FakeClock:
    """A monotonic clock that only moves when the rate limiter sleeps on it (no real wait)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


# --- The fake OLX server ----------------------------------------------------------------


class FakeOlx:
    """Fake OLX API for one shop, modelled on the recorded behaviour.

    `listings` maps listing id -> (category_id, top_category_id). Rows come back sorted by
    id. Quirks copied from the real API:

    - `max_page`: a page number above it is clamped to it, and `meta.current_page` says so
      (the duplicate-pagination bug).
    - `pinned`: these ids are added in front of page 1 of every query they match, on top
      of `per_page` (they also appear again at their normal position).
    - `sub_category_cap`: the search aggregation lists at most this many sub-categories
      per top category, largest first.
    - `hidden`: ids counted in every total but never returned in any page.
    """

    def __init__(
        self,
        listings: dict[int, tuple[int, int]],
        *,
        max_page: int = 50,
        pinned: tuple[int, ...] = (),
        sub_category_cap: int = 20,
        hidden: frozenset[int] = frozenset(),
    ) -> None:
        self.listings = dict(listings)
        self.max_page = max_page
        self.pinned = pinned
        self.sub_category_cap = sub_category_cap
        self.hidden = hidden
        self.requests: list[httpx.Request] = []
        self.on_listings_request: Any = None  # optional hook, called before answering

    def row(self, listing_id: int) -> dict[str, Any]:
        category_id, top_category_id = self.listings[listing_id]
        return {
            "id": listing_id,
            "category_id": category_id,
            "top_category_id": str(top_category_id),  # OLX sends this one as a string
            "pinned": listing_id in self.pinned or None,
            "user_id": SHOP_ID,
        }

    def matching(self, category_id: int | None) -> list[int]:
        return sorted(
            listing_id
            for listing_id, (leaf, top) in self.listings.items()
            if category_id is None or category_id in (leaf, top)
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = request.url.params
        if path == "/users/martom":
            return httpx.Response(200, json={"data": {"id": SHOP_ID, "username": "Martom"}})
        if path == "/search":
            assert params["user_id"] == str(SHOP_ID)
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "meta": {"total": len(self.listings)},
                    "aggregations": {"categories": self.aggregation()},
                },
            )
        if path == "/users/martom/listings":
            if self.on_listings_request is not None:
                self.on_listings_request(self, params)
            return httpx.Response(200, json=self.listings_page(params))
        return httpx.Response(404, json={"error": {"status": 404}})

    def aggregation(self) -> list[dict[str, Any]]:
        tops = Counter(top for _, top in self.listings.values())
        leaves = Counter(self.listings.values())
        result = []
        for top, count in sorted(tops.items()):
            subs = sorted(
                ((leaf, n) for (leaf, t), n in leaves.items() if t == top),
                key=lambda pair: -pair[1],
            )[: self.sub_category_cap]
            result.append(
                {
                    "id": top,
                    "name": f"Top {top}",
                    "count": count,
                    "sub_categories": [
                        {"id": leaf, "name": f"Leaf {leaf}", "count": n} for leaf, n in subs
                    ],
                }
            )
        return result

    def listings_page(self, params: httpx.QueryParams) -> dict[str, Any]:
        category_id = int(params["category_id"]) if "category_id" in params else None
        per_page = int(params.get("per_page", 20))
        requested = int(params.get("page", 1))
        ids = self.matching(category_id)
        visible = [i for i in ids if i not in self.hidden]
        last_page = max(1, math.ceil(len(ids) / per_page))
        page = min(requested, self.max_page)
        rows = [self.row(i) for i in visible[(page - 1) * per_page : page * per_page]]
        if page == 1:
            rows = [self.row(i) for i in self.pinned if i in visible] + rows
        meta = {
            "total": len(ids),
            "last_page": last_page,
            "current_page": page,
            "per_page": per_page,
            "selected_category": 0,
        }
        return {"data": rows, "meta": meta}

    def listing_requests(self) -> list[httpx.QueryParams]:
        return [r.url.params for r in self.requests if r.url.path == "/users/martom/listings"]


def make_shop() -> dict[int, tuple[int, int]]:
    """A small shop: top 701 with leaves 881 (30) and 876 (12), top 224 with leaves
    1126 (25), 1103 (8) and 2091 (3)."""
    listings: dict[int, tuple[int, int]] = {}
    next_id = 1000
    for top, leaf, count in [
        (701, 881, 30),
        (701, 876, 12),
        (224, 1126, 25),
        (224, 1103, 8),
        (224, 2091, 3),
    ]:
        for _ in range(count):
            listings[next_id] = (leaf, top)
            next_id += 1
    return listings


def make_client(server: FakeOlx) -> OlxClient:
    clock = FakeClock()
    return OlxClient(
        transport=httpx.MockTransport(server), clock=clock.monotonic, sleep=clock.sleep
    )


# --- The recorded bug -------------------------------------------------------------------


def test_recording_shows_plain_pagination_returns_duplicates(recorded: dict[str, Any]) -> None:
    full_run = recorded["plain_pagination_full_run"]
    assert full_run == {"pages": 129, "rows": 2585, "unique_ids": 996, "meta_total": 2578}


def test_recording_shows_pages_past_50_are_page_50_again(recorded: dict[str, Any]) -> None:
    pages = recorded["plain_pagination"]

    def ids(page: str) -> list[int]:
        return [row["id"] for row in pages[page]["data"]]

    assert ids("51") == ids("50")
    assert ids("129") == ids("50")
    assert ids("49") != ids("50")
    # The clamp is visible in the response: we asked for 51 and 129, OLX answered 50.
    assert pages["51"]["meta"]["current_page"] == 50
    assert pages["129"]["meta"]["current_page"] == 50
    # Even before page 50, consecutive pages overlap (the list shifts between requests).
    assert set(ids("7")) & set(ids("8"))


async def test_plain_pagination_reproduces_the_bug_and_is_reported_incomplete() -> None:
    # 130 listings at 2 per page = 65 pages, but the fake (like OLX) stops at page 50.
    server = FakeOlx({i: (881, 701) for i in range(1, 131)}, max_page=50)

    async with make_client(server) as olx:
        coverage, rows = await page_target(olx, PageTarget.whole_shop(), per_page=2)

    assert coverage.reported_total == 130
    assert coverage.unique_matching == 100
    assert coverage.stop_reason == "page_clamped"
    assert not coverage.complete
    assert len({row["id"] for row in rows}) == 100


async def test_page_target_stops_on_a_repeated_page_even_without_the_clamp_signal() -> None:
    server = FakeOlx({i: (881, 701) for i in range(1, 21)}, max_page=3)
    original = server.listings_page

    def hide_clamp(params: httpx.QueryParams) -> dict[str, Any]:
        body = original(params)
        body["meta"]["current_page"] = int(params.get("page", 1))  # OLX might not say
        return body

    server.listings_page = hide_clamp  # type: ignore[method-assign]

    async with make_client(server) as olx:
        coverage, _ = await page_target(olx, PageTarget.whole_shop(), per_page=2)

    assert coverage.stop_reason == "repeated_page"
    assert coverage.pages_fetched == 4  # pages 1-3, then the repeat of page 3
    assert not coverage.complete


# --- Discovery and planning -------------------------------------------------------------


async def test_discover_reads_shop_total_and_category_tree() -> None:
    server = FakeOlx(make_shop(), sub_category_cap=2)

    async with make_client(server) as olx:
        discovery = await discover(olx)

    assert discovery.shop_id == SHOP_ID
    assert discovery.shop_total == 78
    by_id = {top.id: top for top in discovery.top_categories}
    assert set(by_id) == {701, 224}
    assert by_id[224].count == 36
    # Capped at 2, so leaf 2091 (3 listings) is missing from the aggregation.
    assert [(c.id, c.count) for c in by_id[224].sub_categories] == [(1126, 25), (1103, 8)]


def test_plan_pages_leaves_and_sweeps_tops_whose_sub_categories_are_incomplete(
    recorded: dict[str, Any],
) -> None:
    aggregation = recorded["search_aggregation"]
    discovery = Discovery.from_api(
        shop_id=SHOP_ID,
        shop_total=aggregation["meta"]["total"],
        categories=aggregation["categories"],
    )

    targets = plan_targets(discovery)

    leaf_ids = {t.category_id for t in targets if t.kind == "leaf"}
    top_ids = {t.category_id for t in targets if t.kind == "top"}
    assert 881 in leaf_ids  # Ograde
    assert 1126 in leaf_ids
    # Recorded: 224 reports 1556 listings but its 20 listed sub-categories add up to 1182,
    # and 701 reports 856 but its sub-categories add up to 736. Both need a sweep.
    assert top_ids == {224, 701}
    # Životinje (208): 11 sub-categories adding up to its 93 listings, so no sweep.
    assert 208 not in top_ids
    # Always a last whole-shop sweep, for listings in no reported category.
    assert targets[-1] == PageTarget.whole_shop()
    assert [t.kind for t in targets].count("shop") == 1


# --- The full sync ----------------------------------------------------------------------


async def test_sync_shop_finds_every_listing_once_despite_the_quirks() -> None:
    shop = make_shop()
    # max_page=4 at per_page=10 reaches 40 listings per query: every leaf and top 224 (36)
    # fit, but the whole shop (78) doesn't, like the real shop at per_page=20.
    # Pinned ids repeat on page 1 of each query.
    server = FakeOlx(shop, max_page=4, pinned=(1000, 1050), sub_category_cap=2)

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    shop_sweep = result.targets[-1]
    assert shop_sweep.target.kind == "shop"
    assert shop_sweep.stop_reason == "page_clamped"  # plain pagination alone would fail
    # The first 40 by id, plus pinned 1050, which page 1 carries on top of per_page.
    assert shop_sweep.unique_matching == 41
    assert shop_sweep.rows == 42  # pinned 1000 came twice: on top and at its position
    assert set(result.listings) == set(shop)
    assert result.shop_total == 78
    assert result.unique_count == 78
    assert result.coverage == 1.0
    assert result.is_complete
    # Leaf 2091 was missing from the aggregation; the sweep of top 224 found it.
    assert all(listing_id in result.listings for listing_id in range(1075, 1078))


async def test_sync_shop_pages_each_category_to_the_end() -> None:
    server = FakeOlx(make_shop(), sub_category_cap=20)

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    pages_881 = [p["page"] for p in server.listing_requests() if p.get("category_id") == "881"]
    assert pages_881 == ["1", "2", "3"]  # 30 listings, 10 per page
    coverage_881 = next(c for c in result.targets if c.target.category_id == 881)
    assert coverage_881.reported_total == 30
    assert coverage_881.unique_matching == 30
    assert coverage_881.complete
    # Sub-categories complete for both tops, so no top sweep, only the shop sweep.
    assert {c.target.kind for c in result.targets} == {"leaf", "shop"}


async def test_sync_below_full_coverage_is_incomplete() -> None:
    shop = make_shop()
    server = FakeOlx(shop, hidden=frozenset({1001}))

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    assert result.shop_total == 78
    assert result.unique_count == 77
    assert result.coverage == pytest.approx(77 / 78)
    assert not result.is_complete


async def test_sync_is_incomplete_when_the_shop_total_grows_during_the_run() -> None:
    shop = make_shop()
    server = FakeOlx(shop)
    added = False

    def add_listing_after_discovery(fake: FakeOlx, params: httpx.QueryParams) -> None:
        nonlocal added
        if not added and params.get("category_id") == "881":
            # Tops are planned in id order (224 before 701), so leaf 1126 is already done.
            fake.listings[9999] = (1126, 224)
            added = True

    server.on_listings_request = add_listing_after_discovery

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    assert result.shop_total_at_start == 78
    assert result.shop_total_at_end == 79
    assert result.shop_total == 79
    # The whole-shop sweep at the end finds the new listing, so this run still covers it.
    assert result.unique_count == 79
    assert result.is_complete


async def test_sync_with_an_empty_shop_is_never_complete() -> None:
    server = FakeOlx({})

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    assert result.shop_total == 0
    assert result.unique_count == 0
    assert not result.is_complete


# --- Deactivation gate ------------------------------------------------------------------


def make_result(*, seen: set[int], shop_total: int) -> SyncResult:
    return SyncResult(
        shop_total_at_start=shop_total,
        shop_total_at_end=shop_total,
        listings={listing_id: {"id": listing_id} for listing_id in seen},
        targets=(),
    )


def test_ids_to_deactivate_returns_active_ids_not_seen_at_full_coverage() -> None:
    result = make_result(seen={1, 2, 3}, shop_total=3)

    assert ids_to_deactivate({1, 2, 3, 4, 5}, result) == {4, 5}


def test_nothing_is_deactivated_below_full_coverage() -> None:
    result = make_result(seen={1, 2}, shop_total=3)

    assert ids_to_deactivate({1, 2, 3, 4, 5}, result) == set()


def test_nothing_is_deactivated_when_the_shop_reports_zero_listings() -> None:
    result = make_result(seen=set(), shop_total=0)

    assert ids_to_deactivate({1, 2, 3}, result) == set()


async def test_nothing_is_deactivated_after_a_buggy_plain_pagination_run() -> None:
    # End to end: a shop that only plain pagination can reach (no categories in the
    # aggregation), with the page clamp. The sync can't see everything, so it must
    # refuse to deactivate the listings it didn't see.
    shop = {i: (881, 701) for i in range(1, 131)}
    server = FakeOlx(shop, max_page=5)
    server.aggregation = lambda: []  # type: ignore[method-assign]

    async with make_client(server) as olx:
        result = await sync_shop(olx, per_page=10)

    assert result.unique_count == 50
    assert not result.is_complete
    assert ids_to_deactivate(set(shop), result) == set()


def test_target_coverage_complete_needs_the_last_page_and_every_listing() -> None:
    target = PageTarget.leaf(881, "Ograde")
    done = TargetCoverage(
        target=target, reported_total=3, rows=3, unique_matching=3, pages_fetched=1,
        stop_reason="last_page",
    )  # fmt: skip
    short = TargetCoverage(
        target=target, reported_total=3, rows=2, unique_matching=2, pages_fetched=1,
        stop_reason="last_page",
    )  # fmt: skip
    clamped = TargetCoverage(
        target=target, reported_total=3, rows=3, unique_matching=3, pages_fetched=1,
        stop_reason="page_clamped",
    )  # fmt: skip

    assert done.complete
    assert not short.complete
    assert not clamped.complete
