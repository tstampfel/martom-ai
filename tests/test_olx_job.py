"""Integration tests for `martom sync` (M1-4): OLX fake -> sync job -> real Postgres.

The OLX side is `FakeShop`, a tiny fake API on `httpx.MockTransport` (no network). The
database side is the throwaway Postgres database from `conftest.database_url`, migrated
with the repo's own migrations, so these tests also run in CI against the service
container.
"""

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from martom import cli
from martom.migrate import run_migrations
from martom.olx import job
from martom.olx.allowlist import Allowlist, AllowlistedListing, load_allowlist
from martom.olx.client import OlxClient
from martom.olx.job import (
    RunStats,
    apply_skipped_run,
    deactivation_allowed,
    recheck_allowed,
    recheck_listings,
    run_outcome,
    run_sync,
)
from martom.review.job import run_review_checks

pytestmark = pytest.mark.anyio

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "olx" / "listing_details.json"
ALLOWLISTED_FIXTURE = Path(__file__).parent / "fixtures" / "olx" / "allowlisted_listings.json"
SHOP_ID = 17193
LEAF, TOP = 881, 701
NO_ALLOWLIST = Allowlist(listings=(), fence_categories={LEAF: "Ograde"})
# Fake start times for scheduled runs: one "night" per call, a day apart (R1-8 needs the
# two guard trips of an auto-apply at least 12 h apart).
FIRST_NIGHT = datetime(2026, 12, 1, 2, 0, tzinfo=UTC)
_nights = itertools.count()


def next_night() -> datetime:
    return FIRST_NIGHT + timedelta(days=next(_nights))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    """A monotonic clock that only moves when the rate limiter sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class FakeShop:
    """Fake OLX for one shop with every listing in one leaf category.

    - `listed`: id -> title of the listings the shop's listing pages show.
    - `off_list`: id -> OLX `status` for listings GET /listings/{id} still knows but the
      listing pages don't show. Any other unknown id answers 404.
    - `hidden`: extra listings counted in the shop total but never shown (coverage < 100%).
    - `detail_errors`: id -> HTTP status to answer GET /listings/{id} with.
    - `detail_timeouts`: ids whose GET /listings/{id} times out.
    - `owners`: id -> another user's id for listings that changed owner.
    - `string_ids`: detail bodies carry `id` and `user.id` as strings ("17193"), the way
      OLX already sends `top_category_id`.
    - `abort_on`: path that answers 429 with `Retry-After: inf` (makes the client abort).
    - `errors_on`: path -> HTTP status to answer that exact path with.
    """

    def __init__(self, listed: dict[int, str]) -> None:
        self.listed = dict(listed)
        self.off_list: dict[int, str] = {}
        self.hidden = 0
        self.detail_errors: dict[int, int] = {}
        self.detail_timeouts: set[int] = set()
        self.owners: dict[int, int] = {}
        self.string_ids = False  # send `id` and `user.id` as strings in detail bodies
        self.flaky: dict[int, int] = {}  # id -> how many more GETs answer 500 first
        self.abort_on: str | None = None
        self.errors_on: dict[str, int] = {}
        # id -> a recorded listing-page row, served as is (its own category) instead of
        # the generated one; the id must also be in `listed`.
        self.recorded: dict[int, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def summary_row(self, listing_id: int) -> dict[str, Any]:
        if listing_id in self.recorded:
            return dict(self.recorded[listing_id])
        return {
            "id": listing_id,
            "title": self.listed[listing_id],
            "slug": f"listing-{listing_id}",
            "category_id": LEAF,
            "top_category_id": str(TOP),
            "price": 10,
            "display_price": "10 KM",
            "user_id": SHOP_ID,
            "status": "active",
            "date": 1795683424,
        }

    def detail(self, listing_id: int, status: str) -> dict[str, Any]:
        title = self.listed.get(listing_id, f"Old listing {listing_id}")
        owner = self.owners.get(listing_id, SHOP_ID)
        return {
            "id": str(listing_id) if self.string_ids else listing_id,
            "title": title,
            "slug": f"listing-{listing_id}",
            "short_description": None,
            "additional": {"description": f"<p>{title}: 83 x 200 cm, 27,00 KM</p>"},
            "price": 27,
            "display_price": "27 KM",
            "price_by_agreement": False,
            "state": "new",
            "status": status,
            "available": True,
            "category_id": LEAF,
            "brand": None,
            "attributes": [{"name": "Stanje", "value": "Novo"}],
            "user": {"id": str(owner) if self.string_ids else owner, "username": "Martom"},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.abort_on is not None and path.startswith(self.abort_on):
            return httpx.Response(429, headers={"Retry-After": "inf"})
        if path in self.errors_on:
            return httpx.Response(self.errors_on[path])
        total = len(self.listed) + self.hidden
        if path == "/users/martom":
            return httpx.Response(200, json={"data": {"id": SHOP_ID}})
        if path == "/search":
            categories = [
                {
                    "id": TOP,
                    "name": "Top",
                    "count": total,
                    "sub_categories": [{"id": LEAF, "name": "Leaf", "count": total}],
                }
            ]
            return httpx.Response(
                200, json={"data": [], "aggregations": {"categories": categories}}
            )
        if path == "/users/martom/listings":
            rows = [self.summary_row(i) for i in sorted(self.listed)]
            meta = {"total": total, "last_page": 1, "current_page": 1}
            return httpx.Response(200, json={"data": rows, "meta": meta})
        if path.startswith("/listings/"):
            listing_id = int(path.rsplit("/", 1)[1])
            if listing_id in self.detail_timeouts:
                raise httpx.ConnectTimeout("timed out", request=request)
            if self.flaky.get(listing_id, 0) > 0:
                self.flaky[listing_id] -= 1
                return httpx.Response(500)
            if listing_id in self.detail_errors:
                return httpx.Response(self.detail_errors[listing_id])
            if listing_id in self.listed:
                return httpx.Response(200, json=self.detail(listing_id, "active"))
            if listing_id in self.off_list:
                return httpx.Response(200, json=self.detail(listing_id, self.off_list[listing_id]))
        return httpx.Response(404, json={"error": {"status": 404}})

    def detail_requests(self) -> list[int]:
        return [
            int(r.url.path.rsplit("/", 1)[1])
            for r in self.requests
            if r.url.path.startswith("/listings/")
        ]


def make_client(server: FakeShop, *, max_attempts: int = 1) -> OlxClient:
    clock = FakeClock()
    return OlxClient(
        transport=httpx.MockTransport(server),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=max_attempts,
    )


@pytest.fixture
def db(database_url: str) -> Iterator[str]:
    run_migrations(database_url, REPO_MIGRATIONS)
    yield database_url


@pytest.fixture
async def conn(db: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(db, autocommit=True) as connection:
        yield connection


async def sync(
    conn: psycopg.AsyncConnection,
    server: FakeShop,
    *,
    full: bool = False,
    max_attempts: int = 1,
    scheduled: bool = False,
    at: datetime | None = None,
    allowlist: Allowlist = NO_ALLOWLIST,
    refresh_days: int | None = None,
    detail_cap: int = job.NIGHTLY_DETAIL_CAP,
) -> RunStats:
    """One run; `at` fakes its `started_at` (default: the database's now()).

    No allowlisted listings unless `allowlist` says so, and no rolling refresh unless
    `refresh_days` says so, so the guard's and the rolling slice's GETs don't show up in
    tests about something else.
    """
    async with make_client(server, max_attempts=max_attempts) as olx:
        return await run_sync(
            conn,
            olx,
            kind="full" if full else "nightly",
            scheduled=scheduled,
            now=None if at is None else (lambda: at),
            allowlist=allowlist,
            refresh_days=refresh_days,
            detail_cap=detail_cap,
        )


async def fetch_all(conn: psycopg.AsyncConnection, query: str, *params: Any) -> list[tuple]:
    cursor = await conn.execute(query, params)  # type: ignore[arg-type]
    return await cursor.fetchall()


async def listings(conn: psycopg.AsyncConnection) -> dict[int, dict[str, Any]]:
    rows = await fetch_all(
        conn,
        "SELECT id, title, active, summary_hash, detail_hash, description_text FROM olx_listing",
    )
    return {
        row[0]: {
            "title": row[1],
            "active": row[2],
            "summary_hash": row[3],
            "detail_hash": row[4],
            "description_text": row[5],
        }
        for row in rows
    }


async def last_run(conn: psycopg.AsyncConnection) -> dict[str, Any]:
    cursor = await conn.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 1")
    row = await cursor.fetchone()
    assert row is not None and cursor.description is not None
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


def ten_listings() -> dict[int, str]:
    return {i: f"Listing {i}" for i in range(1, 11)}


# --- Plain runs -------------------------------------------------------------------------


async def test_first_run_stores_every_listing_and_records_the_run(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})

    stats = await sync(conn, server)

    stored = await listings(conn)
    assert set(stored) == {1, 2}
    assert stored[1]["title"] == "Panel 83"
    assert stored[1]["active"] is True
    assert stored[1]["summary_hash"] is not None
    assert stored[1]["description_text"] == "Panel 83: 83 x 200 cm, 27,00 KM"
    run = await last_run(conn)
    assert run["kind"] == "nightly"
    assert run["status"] == "succeeded"
    assert run["finished_at"] is not None
    assert (run["seen"], run["new"], run["fetched"], run["changed"]) == (2, 2, 2, 0)
    assert (run["deactivated"], run["reactivated"], run["errors"]) == (0, 0, 0)
    assert run["shop_total"] == 2
    assert float(run["coverage"]) == 1.0
    assert stats.status == "succeeded"
    assert stats.new == 2


async def test_unchanged_listings_are_not_fetched_again(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    await sync(conn, server)
    server.requests.clear()

    await sync(conn, server)

    assert server.detail_requests() == []
    run = await last_run(conn)
    assert (run["seen"], run["new"], run["fetched"], run["changed"]) == (2, 0, 0, 0)


async def test_a_changed_summary_fetches_the_detail_again(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    await sync(conn, server)
    server.requests.clear()
    server.listed[2] = "Stup 120"

    await sync(conn, server)

    assert server.detail_requests() == [2]
    assert (await listings(conn))[2]["title"] == "Stup 120"
    run = await last_run(conn)
    assert (run["fetched"], run["changed"]) == (1, 1)


async def test_full_run_fetches_every_detail_even_when_unchanged(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    await sync(conn, server)
    server.requests.clear()

    await sync(conn, server, full=True)

    assert sorted(server.detail_requests()) == [1, 2]
    run = await last_run(conn)
    assert run["kind"] == "full"
    assert run["status"] == "succeeded"
    assert (run["fetched"], run["changed"]) == (2, 0)


async def test_failed_detail_fetch_keeps_the_old_summary_hash(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    await sync(conn, server)
    old_hash = (await listings(conn))[2]["summary_hash"]
    server.listed[2] = "Stup 120"
    server.listed[3] = "New listing"
    server.detail_errors = {2: 403, 3: 403}

    await sync(conn, server)

    stored = await listings(conn)
    assert stored[2]["summary_hash"] == old_hash  # retried next run
    assert stored[3]["summary_hash"] is None  # stored from its summary, detail still missing
    run = await last_run(conn)
    assert run["status"] == "partial"  # a fetch still failing after its retries
    assert run["errors"] == 2
    assert run["failed_listing_ids"] == [2, 3]

    server.detail_errors = {}
    server.requests.clear()
    await sync(conn, server)
    assert sorted(server.detail_requests()) == [2, 3]


async def test_a_fetch_that_succeeds_on_retry_keeps_the_run_succeeded(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    server.flaky = {2: 1}  # one 500, then 200

    await sync(conn, server, max_attempts=2)

    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["retried_count"] == 1
    assert (run["errors"], run["failed_listing_ids"]) == (0, None)
    assert (await listings(conn))[2]["summary_hash"] is not None


async def test_a_fetch_that_still_fails_after_its_retries_makes_the_run_partial(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    server.detail_errors = {2: 500}

    stats = await sync(conn, server, max_attempts=2)

    assert stats.status == "partial"
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["failed_listing_ids"] == [2]
    assert (run["errors"], run["retried_count"]) == (1, 0)


async def test_the_next_run_fetches_the_stored_failed_ids_first(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    server.detail_errors = {3: 500}
    await sync(conn, server)
    server.detail_errors = {}
    server.listed[1] = "Panel 103"  # 1 and 2 changed too, so they are fetched as well
    server.listed[2] = "Stup 120"
    server.requests.clear()

    await sync(conn, server)

    assert server.detail_requests() == [3, 1, 2]
    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["failed_listing_ids"] is None


async def test_the_next_run_rechecks_the_stored_failed_ids_first(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 21)})
    await sync(conn, server)
    del server.listed[20]
    server.detail_errors = {20: 503}  # its re-check fails, so it stays active
    await sync(conn, server)
    assert (await last_run(conn))["failed_listing_ids"] == [20]
    server.detail_errors = {}
    del server.listed[19]  # 19 and 20 are both candidates now: 2 of 20 = 10%, allowed
    server.requests.clear()

    await sync(conn, server)

    # Id order would be [19, 20]; 20 failed last run, so it is re-checked first. Nothing
    # changed on the listing pages, so these are the only GET /listings/{id} requests.
    assert server.detail_requests() == [20, 19]
    stored = await listings(conn)
    assert stored[19]["active"] is False
    assert stored[20]["active"] is False


# --- Deactivation -----------------------------------------------------------------------


async def test_candidate_that_is_gone_is_deactivated_and_still_active_one_is_not(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    # 9 and 10 drop off the listing pages; 9 still answers "active", 10 answers 404.
    del server.listed[9], server.listed[10]
    server.hidden = 0
    server.off_list = {9: "active"}
    # Keep coverage at 100%: two new listings replace them.
    server.listed[11] = "New 11"
    server.listed[12] = "New 12"
    server.requests.clear()

    await sync(conn, server)

    stored = await listings(conn)
    assert stored[9]["active"] is True
    assert stored[10]["active"] is False
    assert {9, 10} <= set(server.detail_requests())
    run = await last_run(conn)
    assert (run["deactivation_candidates"], run["deactivated"]) == (2, 1)
    assert run["deactivation_skipped"] is False
    assert run["skipped_listing_ids"] is None


async def test_candidate_with_an_inactive_status_is_deactivated(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.off_list = {10: "finished"}
    server.listed[11] = "New 11"

    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is False


async def test_candidate_check_error_deactivates_nothing(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.listed[11] = "New 11"
    server.detail_errors = {10: 500}

    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is True
    run = await last_run(conn)
    assert run["deactivated"] == 0
    assert run["errors"] == 1
    assert run["status"] == "partial"  # the re-check still failed after its retries
    assert run["failed_listing_ids"] == [10]


async def test_incomplete_coverage_checks_and_deactivates_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.hidden = 2  # the shop reports 11, we see 9
    server.requests.clear()

    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is True
    assert 10 not in server.detail_requests()
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["deactivation_candidates"] == 0
    assert float(run["coverage"]) < 1.0


async def test_listing_that_reappears_is_reactivated(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.listed[11] = "New 11"
    await sync(conn, server)
    assert (await listings(conn))[10]["active"] is False

    server.listed[10] = "Listing 10"
    server.listed[12] = "New 12"  # the shop total grows with it
    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is True
    run = await last_run(conn)
    assert run["reactivated"] == 1
    active = await fetch_all(conn, "SELECT id FROM active_olx_listing WHERE id = 10")
    assert active == [(10,)]


async def test_inactive_listings_are_hidden_from_the_active_view(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.listed[11] = "New 11"

    await sync(conn, server)

    ids = {row[0] for row in await fetch_all(conn, "SELECT id FROM active_olx_listing")}
    assert 10 not in ids
    assert ids == set(range(1, 10)) | {11}


@pytest.mark.parametrize(
    ("would", "active", "allowed"),
    [(0, 0, True), (1, 10, True), (2, 10, False), (10, 100, True), (11, 100, False)],
)
def test_deactivation_allowed_up_to_exactly_ten_percent(
    would: int, active: int, allowed: bool
) -> None:
    assert deactivation_allowed(would, active) is allowed


async def test_exactly_ten_percent_is_deactivated(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]  # 1 of 10 active = 10%

    # Coverage stays 100%: the shop now reports 9 and we see 9.
    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is False
    assert (await last_run(conn))["deactivated"] == 1


async def test_more_than_ten_percent_deactivates_nothing_and_records_the_skip(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[9], server.listed[10]  # 2 of 10 active = 20%

    with caplog.at_level(logging.ERROR, logger="martom.olx.job"):
        stats = await sync(conn, server)

    stored = await listings(conn)
    assert stored[9]["active"] is True
    assert stored[10]["active"] is True
    run = await last_run(conn)
    assert run["status"] == "skipped_guard"
    assert run["deactivated"] == 0
    assert run["deactivation_skipped"] is True
    assert run["would_deactivate"] == 2
    assert run["skipped_listing_ids"] == [9, 10]
    assert stats.deactivation_skipped is True
    assert any("deactivation skipped" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("coverage", "deactivation_skipped", "recheck_skipped", "errors", "expected"),
    [
        (1.0, False, False, 0, "succeeded"),
        (1.2, False, False, 0, "succeeded"),  # saw more than OLX reports: still complete
        (0.99, False, False, 0, "partial"),
        (0.0, False, False, 0, "partial"),  # OLX reported an empty shop
        (1.0, False, False, 1, "partial"),  # a GET still failing after its retries
        (None, False, False, 1, "partial"),
        (1.0, True, False, 0, "skipped_guard"),
        (1.0, False, True, 0, "skipped_guard"),
        (None, False, False, 0, "succeeded"),  # --apply-skipped has no coverage
        (None, False, True, 0, "skipped_guard"),
        # Precedence: a guard skip outranks partial (documented in migration 002).
        (0.5, True, False, 0, "skipped_guard"),
        (0.5, False, True, 0, "skipped_guard"),
        (1.0, True, False, 3, "skipped_guard"),
    ],
)
def test_run_outcome_and_its_precedence(
    coverage: float | None,
    deactivation_skipped: bool,
    recheck_skipped: bool,
    errors: int,
    expected: str,
) -> None:
    stats = RunStats(
        kind="nightly",
        coverage=coverage,
        deactivation_skipped=deactivation_skipped,
        recheck_skipped=recheck_skipped,
        errors=errors,
    )

    assert run_outcome(stats) == expected


# --- Rate-limit aborts ------------------------------------------------------------------


async def test_abort_while_paging_ends_the_run_partial_and_changes_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    before = await listings(conn)
    del server.listed[10]
    server.abort_on = "/users/martom/listings"

    stats = await sync(conn, server)

    assert stats.status == "partial"
    assert await listings(conn) == before
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["finished_at"] is not None
    assert "Retry-After" in run["error"]
    assert run["failed_listing_ids"] is None  # nothing was left over from earlier runs
    assert run["errors"] == 0


async def test_abort_keeps_last_runs_failed_ids_it_did_not_get_to(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    server.detail_errors = {3: 500}
    await sync(conn, server)
    server.detail_errors = {}
    server.abort_on = "/users/martom/listings"

    await sync(conn, server)

    # Listing 3 was never retried, so the run after this one still takes it first.
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["failed_listing_ids"] == [3]


async def test_a_failed_run_keeps_last_runs_failed_ids_at_the_front(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    server.detail_errors = {3: 500}
    await sync(conn, server)
    server.detail_errors = {}
    server.errors_on = {"/search": 403}  # not retryable: the run fails during discovery
    await sync(conn, server)
    failed_run = await last_run(conn)
    assert failed_run["status"] == "failed"
    assert failed_run["failed_listing_ids"] == [3]
    assert failed_run["errors"] == 0
    server.errors_on = {}
    server.listed[1] = "Panel 103"  # changed too, but goes after the carried-over id
    server.requests.clear()

    await sync(conn, server)

    assert server.detail_requests() == [3, 1]
    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["failed_listing_ids"] is None


async def test_abort_during_detail_fetch_saves_what_was_fetched(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    server.detail_errors = {}

    # Listing 2's GET makes OLX send an absurd Retry-After: 1 is fetched, 2 and 3 are not.
    original = server.__call__

    def abort_at_two(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2":
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "inf"})
        return original(request)

    clock = FakeClock()
    async with OlxClient(
        transport=httpx.MockTransport(abort_at_two),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=1,
    ) as olx:
        stats = await run_sync(conn, olx, kind="nightly")

    stored = await listings(conn)
    assert stored[1]["summary_hash"] is not None
    assert stored[2]["summary_hash"] is None
    assert stored[3]["summary_hash"] is None
    assert stats.status == "partial"
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["fetched"] == 1
    # Not yet fetched, stored for the next run to take first; no GET failed for them.
    assert run["failed_listing_ids"] == [2, 3]
    assert run["errors"] == 0


async def test_the_next_run_fetches_the_ids_an_aborted_run_did_not_get_to_first(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    await sync(conn, server)
    for listing_id in (1, 2, 3):
        server.listed[listing_id] += " (novo)"  # all three changed
    server.abort_on = "/listings/2"
    await sync(conn, server)
    assert (await last_run(conn))["failed_listing_ids"] == [2, 3]
    server.listed[1] += " opet"  # changed again, but goes after the carried-over ids
    server.abort_on = None
    server.requests.clear()

    await sync(conn, server)

    assert server.detail_requests() == [2, 3, 1]
    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["failed_listing_ids"] is None


async def test_run_deadline_ends_the_run_partial_with_the_rest_for_the_next_run(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})
    original = server.__call__

    # Listing 2 asks for a 40-minute pause, which would pass the 30-minute deadline.
    def slow_down_at_two(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2":
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "2400"})
        return original(request)

    clock = FakeClock()
    async with OlxClient(
        transport=httpx.MockTransport(slow_down_at_two),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=3,
        run_deadline_seconds=1800,
    ) as olx:
        stats = await run_sync(conn, olx, kind="nightly")

    assert stats.status == "partial"
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert "deadline" in run["error"]
    assert run["fetched"] == 1
    assert run["failed_listing_ids"] == [2, 3]
    assert run["final_request_interval"] == 3  # raised once by the 429
    assert clock.now < 1000.0 + 1800  # it stopped instead of waiting


async def test_a_run_records_the_request_interval_it_ended_with(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    original = server.__call__
    answered = set()

    def one_429(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2" and 2 not in answered:
            answered.add(2)
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "5"})
        return original(request)

    await sync(conn, server)
    assert (await last_run(conn))["final_request_interval"] == 2

    server.listed[2] = "Stup 120"
    clock = FakeClock()
    async with OlxClient(
        transport=httpx.MockTransport(one_429),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=2,
    ) as olx:
        await run_sync(conn, olx, kind="nightly")

    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["final_request_interval"] == 3
    assert run["retried_count"] == 1


async def test_the_run_summary_log_says_how_long_olx_asked_us_to_wait(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    original = server.__call__
    answered = set()

    def one_429(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2" and 2 not in answered:
            answered.add(2)
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "600"})
        return original(request)

    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        async with OlxClient(
            transport=httpx.MockTransport(one_429),
            clock=clock.monotonic,
            sleep=clock.sleep,
            max_attempts=2,
        ) as olx:
            await run_sync(conn, olx, kind="nightly")

    [summary] = [r for r in caplog.records if getattr(r, "event", None) == "sync_run"]
    assert summary.olx_429_count == 1
    assert summary.olx_429_requested_wait_seconds == 600.0
    assert summary.olx_requests_sent == len(server.requests)
    assert summary.olx_elapsed_seconds == clock.now - 1000.0
    assert "1 x 429, OLX asked to wait 600 s" in summary.getMessage()


async def test_a_run_that_crashes_still_logs_its_summary_then_re_raises(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    answered = set()

    def one_429_then_crash(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/listings/1" and path not in answered:
            answered.add(path)
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "30"})
        if path == "/listings/2":
            server.requests.append(request)
            raise RuntimeError("database or network gone")  # not an OLX error: a crash
        return server(request)

    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        async with OlxClient(
            transport=httpx.MockTransport(one_429_then_crash),
            clock=clock.monotonic,
            sleep=clock.sleep,
            max_attempts=2,
        ) as olx:
            with pytest.raises(RuntimeError, match="network gone"):
                await run_sync(conn, olx, kind="nightly")

    [summary] = [r for r in caplog.records if getattr(r, "event", None) == "sync_run"]
    assert summary.levelno == logging.ERROR
    assert summary.status == "failed"
    assert summary.error == "RuntimeError: database or network gone"
    assert summary.olx_429_count == 1
    assert summary.olx_429_requested_wait_seconds == 30.0
    assert summary.olx_requests_sent == len(server.requests)
    assert summary.olx_elapsed_seconds == clock.now - 1000.0
    assert (await last_run(conn))["status"] == "failed"  # unchanged by the summary


async def test_a_client_reused_across_runs_logs_each_runs_own_olx_numbers(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    answered = set()

    def one_429(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2" and 2 not in answered:
            answered.add(2)
            server.requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "600"})
        return server(request)

    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        async with OlxClient(
            transport=httpx.MockTransport(one_429),
            clock=clock.monotonic,
            sleep=clock.sleep,
            max_attempts=2,
        ) as olx:
            await run_sync(conn, olx, kind="nightly")  # the 429 lands in this run
            first_run_requests = len(server.requests)
            second_run_start = clock.now
            server.listed[2] = "Stup 120"  # one changed summary: one detail GET
            await run_sync(conn, olx, kind="nightly")

    first, second = [r for r in caplog.records if getattr(r, "event", None) == "sync_run"]
    assert first.olx_429_count == 1
    assert first.olx_requests_sent == first_run_requests
    assert second.olx_429_count == 0
    assert second.olx_429_requested_wait_seconds == 0.0
    assert second.olx_requests_sent == len(server.requests) - first_run_requests
    assert second.olx_elapsed_seconds == clock.now - second_run_start
    assert second.retried_count == 0
    assert "0 x 429, OLX asked to wait 0 s" in second.getMessage()


async def test_abort_during_candidate_check_deactivates_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.abort_on = "/listings/10"

    stats = await sync(conn, server)

    assert (await listings(conn))[10]["active"] is True
    assert stats.status == "partial"
    run = await last_run(conn)
    assert run["status"] == "partial"
    assert run["deactivated"] == 0
    assert run["failed_listing_ids"] == [10]  # re-checked first next run


# --- Applying skipped deactivations --------------------------------------------------


async def trip_guard(
    conn: psycopg.AsyncConnection,
    server: FakeShop,
    gone: set[int],
    *,
    scheduled: bool = True,
    at: datetime | None = None,
) -> int:
    """Run a sync where `gone` dropped off the listing pages; it must trip the 10% guard.

    By default a scheduled run on the next fake night (a day after the previous one).
    """
    for listing_id in gone:
        server.listed.pop(listing_id, None)
    if at is None and scheduled:
        at = next_night()
    await sync(conn, server, scheduled=scheduled, at=at)
    run = await last_run(conn)
    assert run["deactivation_skipped"] is True, run
    assert run["status"] == "skipped_guard"
    return int(run["id"])


async def apply_skipped(conn: psycopg.AsyncConnection, server: FakeShop, run_id: int) -> RunStats:
    async with make_client(server) as olx:
        return await apply_skipped_run(conn, olx, run_id)


async def test_manual_apply_deactivates_the_stored_ids_that_are_gone(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    skipped_run = await trip_guard(conn, server, {8, 9, 10})
    server.off_list = {8: "active"}  # OLX says 8 is still for sale: never deactivated
    server.owners = {9: 99999}
    server.off_list[9] = "active"  # 9 moved to another user: not Martom's any more

    stats = await apply_skipped(conn, server, skipped_run)

    stored = await listings(conn)
    assert stored[8]["active"] is True
    assert stored[9]["active"] is False
    assert stored[10]["active"] is False
    assert stats.status == "succeeded"
    run = await last_run(conn)
    assert run["kind"] == "apply_skipped"
    assert run["applied_run_ids"] == [skipped_run]
    assert (run["deactivation_candidates"], run["deactivated"]) == (3, 2)


async def test_manual_apply_refuses_a_run_that_skipped_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    normal_run = int((await last_run(conn))["id"])

    stats = await apply_skipped(conn, server, normal_run)

    assert stats.status == "failed"
    assert "no skipped deactivations" in (await last_run(conn))["error"]


@pytest.mark.parametrize("failure", ["429", "500", "timeout"])
async def test_recheck_error_leaves_the_listing_active(
    conn: psycopg.AsyncConnection, failure: str
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 31)})
    await sync(conn, server)
    skipped_run = await trip_guard(conn, server, {26, 27, 28, 29, 30})
    if failure == "timeout":
        server.detail_timeouts = {30}
    else:
        server.detail_errors = {30: int(failure)}

    await apply_skipped(conn, server, skipped_run)

    stored = await listings(conn)
    assert stored[30]["active"] is True
    assert all(stored[i]["active"] is False for i in (26, 27, 28, 29))
    run = await last_run(conn)
    assert (run["deactivated"], run["errors"]) == (4, 1)
    assert run["recheck_skipped"] is False


async def test_more_than_20_percent_recheck_failures_applies_nothing(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 31)})
    await sync(conn, server)
    skipped_run = await trip_guard(conn, server, {26, 27, 28, 29, 30})
    server.detail_errors = {29: 503, 30: 429}  # 2 of 5 = 40%

    with caplog.at_level(logging.ERROR, logger="martom.olx.job"):
        await apply_skipped(conn, server, skipped_run)

    stored = await listings(conn)
    assert all(stored[i]["active"] is True for i in (26, 27, 28, 29, 30))
    run = await last_run(conn)
    assert run["status"] == "skipped_guard"
    assert run["deactivated"] == 0
    assert run["recheck_skipped"] is True
    assert any("re-check failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("errors", "total", "allowed"),
    [(0, 0, True), (1, 5, True), (2, 10, True), (2, 5, False), (3, 10, False)],
)
def test_recheck_allowed_up_to_exactly_20_percent(errors: int, total: int, allowed: bool) -> None:
    assert recheck_allowed(errors, total) is allowed


async def test_second_consecutive_trip_applies_only_the_intersection(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    first = await trip_guard(conn, server, {8, 9, 10})
    server.listed[8] = "Listing 8"  # back on the pages
    server.requests.clear()

    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        second = await trip_guard(conn, server, {6, 7, 9, 10})  # trips again: 40%

    stored = await listings(conn)
    # Flagged in both trips: 9 and 10, deactivated by the second trip itself. 6 and 7 were
    # flagged only once, so the 10% guard keeps them (and stores them for the next run).
    assert stored[9]["active"] is False
    assert stored[10]["active"] is False
    assert stored[6]["active"] is True
    assert stored[7]["active"] is True
    assert stored[8]["active"] is True
    assert {9, 10} <= set(server.detail_requests())  # re-checked with a GET first
    run = await last_run(conn)
    assert run["id"] == second
    assert run["status"] == "skipped_guard"
    assert run["deactivated"] == 2
    assert run["applied_run_ids"] == [second, first]
    assert run["would_deactivate"] == 4
    assert run["skipped_listing_ids"] == [6, 7]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(first) in m and str(second) in m and "auto-apply" in m for m in warnings)
    messages = [r.getMessage() for r in caplog.records]
    assert any("listing 9 deactivated: 404" in m for m in messages)
    assert any("listing 10 deactivated: 404" in m for m in messages)


async def test_auto_apply_never_deactivates_a_listing_olx_says_is_active(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await trip_guard(conn, server, {8, 9, 10})
    server.off_list = {9: "active"}

    await trip_guard(conn, server, {8, 9, 10})  # 8 and 10 confirmed gone: 20%, trips

    stored = await listings(conn)
    assert stored[9]["active"] is True
    assert stored[8]["active"] is False
    assert stored[10]["active"] is False


async def test_no_auto_apply_when_the_trips_are_not_consecutive(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await trip_guard(conn, server, {9, 10})
    server.listed[9] = "Listing 9"
    server.listed[10] = "Listing 10"
    await sync(conn, server, scheduled=True, at=next_night())  # a normal scheduled run

    await trip_guard(conn, server, {9, 10})

    stored = await listings(conn)
    assert stored[9]["active"] is True
    assert stored[10]["active"] is True
    run = await last_run(conn)
    assert run["deactivated"] == 0
    assert run["applied_run_ids"] is None


@pytest.mark.parametrize(("hours", "applied"), [(11, False), (12, True)])
async def test_auto_apply_needs_the_previous_trip_at_least_12_hours_earlier(
    conn: psycopg.AsyncConnection, hours: int, applied: bool
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    first_at = next_night()
    await trip_guard(conn, server, {9, 10}, at=first_at)

    await trip_guard(conn, server, {9, 10}, at=first_at + timedelta(hours=hours))

    stored = await listings(conn)
    assert (stored[9]["active"], stored[10]["active"]) == (not applied, not applied)
    run = await last_run(conn)
    assert run["deactivated"] == (2 if applied else 0)
    assert run["skipped_listing_ids"] == (None if applied else [9, 10])


async def test_a_manual_run_never_auto_applies(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await trip_guard(conn, server, {9, 10})

    await trip_guard(conn, server, {9, 10}, scheduled=False)  # by hand, the next morning

    stored = await listings(conn)
    assert stored[9]["active"] is True
    assert stored[10]["active"] is True
    run = await last_run(conn)
    assert run["scheduled"] is False
    assert run["applied_run_ids"] is None
    assert run["skipped_listing_ids"] == [9, 10]  # still there for --apply-skipped


async def test_a_manual_run_between_two_scheduled_trips_keeps_the_streak(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    first = await trip_guard(conn, server, {9, 10})
    await trip_guard(conn, server, {9, 10}, scheduled=False)  # someone checks by hand
    server.listed[9] = "Listing 9"
    await sync(conn, server)  # and a clean manual run, too
    del server.listed[9]

    second = await trip_guard(conn, server, {9, 10})

    stored = await listings(conn)
    assert stored[9]["active"] is False
    assert stored[10]["active"] is False
    run = await last_run(conn)
    assert run["applied_run_ids"] == [second, first]


async def test_auto_apply_with_too_many_recheck_failures_applies_nothing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await trip_guard(conn, server, {8, 9, 10})
    server.detail_errors = {9: 502}  # 1 of the 3 flagged ids fails its re-check: 33%

    await trip_guard(conn, server, {8, 9, 10})  # 8 and 10 confirmed gone: 20%, trips

    stored = await listings(conn)
    assert all(stored[i]["active"] is True for i in (8, 9, 10))
    run = await last_run(conn)
    assert run["status"] == "skipped_guard"
    assert run["recheck_skipped"] is True
    assert run["deactivated"] == 0
    assert run["applied_run_ids"] is None


async def test_string_ids_in_the_recheck_still_count_as_martoms_listing(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.listed[11] = "New 11"
    server.off_list = {10: "active"}  # missed by the pages, still for sale
    server.string_ids = True

    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is True
    run = await last_run(conn)
    assert (run["deactivated"], run["errors"]) == (0, 0)


async def test_string_owner_id_of_another_user_is_not_martom(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.off_list = {10: "active"}
    server.owners = {10: 99999}
    server.string_ids = True

    await sync(conn, server)

    assert (await listings(conn))[10]["active"] is False


async def test_recorded_detail_body_is_recognised_as_martoms_active_listing() -> None:
    # The real GET /listings/{id} body (fixture, `user` object trimmed to public fields).
    recorded = json.loads(DETAIL_FIXTURE.read_text())["details"]["63996799"]
    assert recorded["user"]["id"] == SHOP_ID

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=recorded)

    clock = FakeClock()
    async with OlxClient(
        transport=httpx.MockTransport(answer), clock=clock.monotonic, sleep=clock.sleep
    ) as olx:
        result = await recheck_listings(olx, [63996799], SHOP_ID)

    assert result.still_active == {63996799}
    assert result.gone == {}
    assert result.errors == {}


async def test_applying_a_run_twice_logs_and_counts_nothing_the_second_time(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    skipped_run = await trip_guard(conn, server, {9, 10})
    await apply_skipped(conn, server, skipped_run)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        await apply_skipped(conn, server, skipped_run)

    assert not any("deactivated:" in r.getMessage() for r in caplog.records)
    assert (await last_run(conn))["deactivated"] == 0


async def test_details_are_saved_in_batches_so_a_crash_keeps_earlier_ones(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job, "DETAIL_BATCH_SIZE", 1)
    server = FakeShop({1: "Panel 83", 2: "Stup 100", 3: "Kopča"})

    def crash_at_two(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listings/2":
            raise RuntimeError("database or network gone")
        return server(request)

    clock = FakeClock()
    async with OlxClient(
        transport=httpx.MockTransport(crash_at_two),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=1,
    ) as olx:
        with pytest.raises(RuntimeError):
            await run_sync(conn, olx, kind="nightly")

    stored = await listings(conn)
    assert stored[1]["summary_hash"] is not None  # its batch was saved before the crash
    assert stored[2]["summary_hash"] is None
    assert stored[3]["summary_hash"] is None  # seen and stored, detail still to fetch
    run = await last_run(conn)
    assert run["status"] == "failed"
    assert run["fetched"] == 1


async def test_second_trip_that_applies_every_confirmed_id_leaves_nothing_skipped(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await trip_guard(conn, server, {8, 9, 10})

    second = await trip_guard(conn, server, {8, 9, 10})

    stored = await listings(conn)
    assert all(stored[i]["active"] is False for i in (8, 9, 10))
    run = await last_run(conn)
    assert run["id"] == second
    assert run["deactivated"] == 3
    # The guard did trip and a mass deactivation bypassed it, so it alerts on purpose.
    assert run["status"] == "skipped_guard"
    assert run["skipped_listing_ids"] is None


async def test_listing_with_a_new_owner_is_deactivated_as_not_martom(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    del server.listed[10]
    server.off_list = {10: "active"}
    server.owners = {10: 99999}

    with caplog.at_level(logging.INFO, logger="martom.olx.job"):
        await sync(conn, server)

    assert (await listings(conn))[10]["active"] is False
    assert any("listing 10 deactivated: not_martom" in r.getMessage() for r in caplog.records)


# --- Allowlist guard (#45) ----------------------------------------------------------------

# One allowlisted listing in leaf 1126, for the tests that build their own case.
TENSIONER = AllowlistedListing(id=18129143, category_id=1126, name="Španer")
ONE_ALLOWLISTED = Allowlist(listings=(TENSIONER,), fence_categories={LEAF: "Ograde"})


def shop_with_allowlisted_listings() -> FakeShop:
    """Ten ordinary listings plus the three allowlisted ones, as OLX listed them (recorded)."""
    server = FakeShop(ten_listings())
    for row in json.loads(ALLOWLISTED_FIXTURE.read_text(encoding="utf-8"))["rows"]:
        server.listed[row["id"]] = row["title"]
        server.recorded[row["id"]] = row
    return server


def shop_with_tensioner_in(category_id: int) -> FakeShop:
    server = FakeShop(ten_listings())
    server.listed[TENSIONER.id] = "ŠPANER"
    server.recorded[TENSIONER.id] = {
        **server.summary_row(1),
        "id": TENSIONER.id,
        "title": "ŠPANER",
        "category_id": category_id,
        "top_category_id": "224",
    }
    return server


async def test_every_allowlisted_listing_is_synced_and_stored(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = shop_with_allowlisted_listings()

    with caplog.at_level(logging.INFO):
        stats = await sync(conn, server, allowlist=load_allowlist())  # the committed file

    stored = await listings(conn)
    for entry in load_allowlist().listings:
        assert entry.id in stored, entry
        assert stored[entry.id]["active"] is True
        assert stored[entry.id]["detail_hash"] is not None
    run = await last_run(conn)
    assert run["status"] == "succeeded"
    # 81645615 is recorded in 881 (Ograde), not in 876: a move into a fence category is
    # an info line, not a warning.
    assert run["allowlist_warnings"] == []
    assert stats.allowlist_warnings == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        getattr(r, "event", None) == "allowlist_listing_in_fence_category"
        and r.listing_id == 81645615  # type: ignore[attr-defined]
        for r in caplog.records
    )


async def test_allowlisted_listings_are_stored_once_though_several_targets_return_them(
    conn: psycopg.AsyncConnection,
) -> None:
    # R1-1: the dedupe is in `sync_shop` (by listing id), before anything is stored, so
    # check what a broken dedupe would change: the seen/new counts and one detail GET per id.
    server = shop_with_allowlisted_listings()
    allowlisted = [entry.id for entry in load_allowlist().listings]

    stats = await sync(conn, server, allowlist=load_allowlist())

    # The fake answers every listing-page query with every row, so leaf 881 and the
    # whole-shop sweep both return each allowlisted listing: two copies per id.
    page_requests = [r for r in server.requests if r.url.path == "/users/martom/listings"]
    targets_paged = sum(1 for r in page_requests if r.url.params.get("per_page") != "1")
    assert targets_paged >= 2
    assert (stats.seen, stats.new) == (13, 13)  # 10 ordinary + 3 allowlisted, once each
    details = server.detail_requests()
    assert all(details.count(listing_id) == 1 for listing_id in allowlisted)


async def test_seen_allowlisted_listing_is_not_fetched_again_by_the_guard(
    conn: psycopg.AsyncConnection,
) -> None:
    server = shop_with_tensioner_in(1126)
    await sync(conn, server, allowlist=ONE_ALLOWLISTED)
    server.requests.clear()

    await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    assert server.detail_requests() == []


async def test_allowlisted_listing_that_answers_404_is_warned_and_recorded(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())  # the tensioner is not listed, so GET answers 404

    with caplog.at_level(logging.WARNING, logger="martom.olx.job"):
        stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    run = await last_run(conn)
    assert run["allowlist_warnings"] == [
        {
            "listing_id": 18129143,
            "name": "Španer",
            "reason": "404",
            "expected_category_id": 1126,
            "category_id": None,
            "error": None,
        }
    ]
    # A warning never fails the run.
    assert run["status"] == "succeeded"
    assert stats.status == "succeeded"
    assert run["errors"] == 0
    warning = next(r for r in caplog.records if "18129143" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert "404" in warning.getMessage()
    assert warning.event == "allowlist_warning"  # type: ignore[attr-defined]


async def test_allowlisted_listing_seen_in_another_category_is_only_logged(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    # R1-3: 1126 -> 1188 is still a category the sync pages, so the listing is synced.
    server = shop_with_tensioner_in(1188)

    with caplog.at_level(logging.INFO):
        await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    run = await last_run(conn)
    assert run["status"] == "succeeded"
    assert run["allowlist_warnings"] == []
    assert (await listings(conn))[18129143]["active"] is True  # stored as usual
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        r.getMessage() == "18129143 now found via category 1188 (allowlisted in 1126); still synced"
        and r.levelno == logging.INFO
        for r in caplog.records
    )


async def test_allowlisted_row_without_a_category_is_recorded_but_not_a_move(
    conn: psycopg.AsyncConnection,
) -> None:
    server = shop_with_tensioner_in(1126)
    del server.recorded[TENSIONER.id]["category_id"]

    await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    [warning] = (await last_run(conn))["allowlist_warnings"]
    assert (warning["reason"], warning["error"]) == ("recheck_failed", "no_category_id")


async def test_allowlisted_listing_moved_into_ograde_raises_no_warning(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = shop_with_tensioner_in(LEAF)  # 881, Ograde: covered by the category sync

    with caplog.at_level(logging.INFO):
        await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    run = await last_run(conn)
    assert run["allowlist_warnings"] == []
    assert run["status"] == "succeeded"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        r.getMessage() == "18129143 now found via category Ograde; allowlist entry redundant"
        and r.levelno == logging.INFO
        for r in caplog.records
    )


async def test_allowlisted_listing_missing_from_the_pages_but_active_is_not_seen(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    server.off_list = {TENSIONER.id: "active"}

    await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    [warning] = (await last_run(conn))["allowlist_warnings"]
    assert warning["reason"] == "not_seen"


async def allowlist_issue_kinds(conn: psycopg.AsyncConnection) -> list[str]:
    """Run the review checks (as `martom extract` does) and return the allowlist issues."""
    await run_review_checks(conn, today=date(2026, 11, 27))
    rows = await fetch_all(
        conn, "SELECT kind FROM review_issue WHERE kind LIKE 'allowlist%%' AND status = 'open'"
    )
    return [row[0] for row in rows]


async def test_not_seen_on_a_partial_coverage_run_is_recorded_but_not_raised_to_toni(
    conn: psycopg.AsyncConnection,
) -> None:
    # R1A-1: the sync stopped short of 100% (a listing it never reached), so a listing
    # that OLX says is active may just have been on a page it didn't get to.
    server = FakeShop(ten_listings())
    server.hidden = 1
    server.off_list = {TENSIONER.id: "active"}

    stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    assert stats.status == "partial"
    [warning] = (await last_run(conn))["allowlist_warnings"]
    assert warning["reason"] == "not_seen"  # still recorded and logged for Tomislav
    assert await allowlist_issue_kinds(conn) == []


async def test_not_seen_on_a_full_coverage_run_is_raised_to_toni(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    server.off_list = {TENSIONER.id: "active"}

    stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    assert stats.coverage == 1
    assert await allowlist_issue_kinds(conn) == ["allowlist_listing_not_synced"]


@pytest.mark.parametrize(("status", "reason"), [("inactive", "inactive"), ("sold", "inactive")])
async def test_allowlisted_listing_that_is_no_longer_active_is_warned(
    conn: psycopg.AsyncConnection, status: str, reason: str
) -> None:
    server = FakeShop(ten_listings())
    server.off_list = {TENSIONER.id: status}

    await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    [warning] = (await last_run(conn))["allowlist_warnings"]
    assert warning["reason"] == reason


async def test_allowlisted_listing_whose_check_fails_is_warned_but_counts_no_error(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    server.detail_errors = {TENSIONER.id: 503}

    stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    [warning] = (await last_run(conn))["allowlist_warnings"]
    assert (warning["reason"], warning["error"]) == ("recheck_failed", "5xx")
    assert stats.status == "succeeded"
    assert stats.errors == 0
    assert stats.failed_listing_ids is None


async def test_allowlist_warning_never_deactivates_other_listings(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    assert stats.deactivated == 0
    assert all(row["active"] for row in (await listings(conn)).values())
    assert (await last_run(conn))["allowlist_warnings"][0]["reason"] == "404"


async def test_run_that_breaks_before_the_guard_records_no_allowlist_check(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    server.errors_on = {"/search": 500}

    stats = await sync(conn, server, allowlist=ONE_ALLOWLISTED)

    assert stats.status == "failed"
    # NULL, not []: the review check must not read "no warnings" from a run that never
    # looked (it takes the latest run with a non-NULL value).
    assert (await last_run(conn))["allowlist_warnings"] is None


async def test_run_sync_reads_the_committed_allowlist_by_default(
    conn: psycopg.AsyncConnection,
) -> None:
    server = shop_with_allowlisted_listings()

    async with make_client(server) as olx:
        stats = await run_sync(conn, olx, kind="nightly")

    assert stats.allowlist_warnings == []


# --- Rolling refresh (#135) -------------------------------------------------------------


async def age_by_one_day(conn: psycopg.AsyncConnection) -> None:
    """Move every stored timestamp a day into the past: the next run is "the next night"."""
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = detail_fetched_at - interval '1 day', "
        "first_seen_at = first_seen_at - interval '1 day'"
    )


async def oldest_ids(conn: psycopg.AsyncConnection, limit: int) -> list[int]:
    rows = await fetch_all(
        conn,
        "SELECT id FROM olx_listing WHERE active "
        "ORDER BY detail_fetched_at ASC NULLS FIRST, id LIMIT %s",
        limit,
    )
    return [row[0] for row in rows]


def test_rolling_slice_is_one_seventh_of_the_active_listings_rounded_up() -> None:
    assert job.rolling_slice_size(15, refresh_days=7) == 3
    assert job.rolling_slice_size(14, refresh_days=7) == 2
    assert job.rolling_slice_size(2600, refresh_days=7) == 372
    assert job.rolling_slice_size(0, refresh_days=7) == 0


async def test_rolling_refresh_covers_every_listing_within_seven_nights_oldest_first(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 16)})  # 15 listings: 3 a night
    await sync(conn, server)  # every listing new, so every detail fetched once
    refreshed_on: dict[int, list[int]] = {i: [] for i in server.listed}

    for night in range(1, 9):
        await age_by_one_day(conn)
        expected = await oldest_ids(conn, 3)
        server.requests.clear()

        stats = await sync(conn, server, refresh_days=7)

        assert server.detail_requests() == expected, f"night {night}"
        assert (stats.rolling_refreshed, stats.rolling_deferred) == (3, 0)
        for listing_id in expected:
            refreshed_on[listing_id].append(night)

    assert all(1 <= nights[0] <= 7 for nights in refreshed_on.values()), refreshed_on
    run = await last_run(conn)
    assert (run["rolling_refreshed"], run["rolling_deferred"]) == (3, 0)
    assert run["status"] == "succeeded"
    assert float(run["oldest_detail_age_days"]) < 7


async def test_changed_listings_are_never_in_the_rolling_slice(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 16)})
    await sync(conn, server)
    await age_by_one_day(conn)
    server.listed[1] = "Listing 1, new title"  # changed, and among the oldest
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7)

    assert server.detail_requests() == [1, 2, 3, 4]  # 1 as changed, then the oldest 3
    assert (stats.fetched, stats.changed, stats.rolling_refreshed) == (4, 1, 3)


async def test_changed_listings_over_the_cap_are_all_fetched_and_the_slice_deferred(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    for listing_id in (2, 4, 6, 8):
        server.listed[listing_id] += " (edited)"
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7, detail_cap=3)

    assert sorted(server.detail_requests()) == [2, 4, 6, 8]  # all four, though the cap is 3
    assert (stats.rolling_refreshed, stats.rolling_deferred) == (0, 2)  # ceil(10 / 7) = 2
    run = await last_run(conn)
    assert (run["rolling_refreshed"], run["rolling_deferred"]) == (0, 2)
    assert run["status"] == "succeeded"  # a deferred slice doesn't change the status


async def test_the_rolling_slice_gets_only_what_is_left_of_the_cap(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    server.listed[10] += " (edited)"
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7, detail_cap=2)

    assert server.detail_requests() == [10, 1]
    assert (stats.rolling_refreshed, stats.rolling_deferred) == (1, 1)


async def test_a_full_run_has_no_rolling_slice(conn: psycopg.AsyncConnection) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    server.requests.clear()

    stats = await sync(conn, server, full=True, refresh_days=7, detail_cap=3)

    assert sorted(server.detail_requests()) == list(range(1, 11))  # the cap is nightly only
    assert (stats.rolling_refreshed, stats.rolling_deferred) == (0, 0)


async def test_a_rolling_get_that_fails_is_an_error_and_is_retried_first_next_night(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    server.detail_errors[1] = 500
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7)

    assert server.detail_requests() == [1, 2]
    assert (stats.rolling_refreshed, stats.errors, stats.status) == (1, 1, "partial")
    assert stats.failed_listing_ids == [1]
    assert (await listings(conn))[1]["active"] is True  # a detail GET never deactivates

    del server.detail_errors[1]
    server.requests.clear()
    await sync(conn, server, refresh_days=7)

    assert server.detail_requests()[0] == 1  # still the oldest, so first again


async def test_abort_during_the_rolling_slice_carries_its_ids_to_the_next_run(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    server.abort_on = "/listings/"

    stats = await sync(conn, server, refresh_days=7)

    assert stats.status == "partial"
    assert stats.failed_listing_ids == [1, 2]
    assert stats.errors == 0  # no GET failed: OLX stopped the run


async def test_the_run_records_the_oldest_detail_age_among_active_listings(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = now() - interval '5 days' WHERE id = 3"
    )
    # An inactive listing is never refreshed and doesn't count.
    await conn.execute(
        "INSERT INTO olx_listing (id, summary, active, detail_fetched_at) "
        "VALUES (99, '{}', false, now() - interval '30 days')"
    )

    with caplog.at_level(logging.WARNING, logger="martom.olx.job"):
        stats = await sync(conn, server)

    assert stats.oldest_detail_age_days == pytest.approx(5, abs=0.01)
    assert stats.detail_refresh_overdue is False
    run = await last_run(conn)
    assert float(run["oldest_detail_age_days"]) == pytest.approx(5, abs=0.01)
    assert run["detail_refresh_overdue"] is False
    assert not [r for r in caplog.records if getattr(r, "event", None) == "detail_refresh_overdue"]


async def test_a_detail_older_than_8_days_logs_a_warning_and_flags_the_run(
    conn: psycopg.AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = now() - interval '8 days 1 hour' WHERE id = 3"
    )

    with caplog.at_level(logging.WARNING, logger="martom.olx.job"):
        stats = await sync(conn, server)

    assert stats.detail_refresh_overdue is True
    assert stats.status == "succeeded"  # the alert (#32) reads the flag, not the status
    run = await last_run(conn)
    assert run["detail_refresh_overdue"] is True
    assert float(run["oldest_detail_age_days"]) > 8
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "detail_refresh_overdue"]
    assert record.levelno == logging.WARNING
    assert record.overdue_listing_ids == [3]  # type: ignore[attr-defined]
    assert record.getMessage().endswith("oldest listings: 3")


async def test_a_listing_never_fetched_counts_from_when_it_was_first_seen(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = NULL, "
        "first_seen_at = now() - interval '10 days' WHERE id = 4"
    )

    stats = await sync(conn, server)

    assert stats.oldest_detail_age_days == pytest.approx(10, abs=0.01)
    assert stats.detail_refresh_overdue is True


async def test_a_run_olx_stopped_early_still_records_the_oldest_age(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = now() - interval '9 days' WHERE id = 3"
    )
    server.abort_on = "/users/martom/listings"

    stats = await sync(conn, server)

    assert stats.status == "partial"
    assert stats.detail_refresh_overdue is True


async def test_an_olx_stop_inside_the_rolling_slice_still_rechecks_and_deactivates(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    del server.listed[5]  # sold: GET /listings/5 answers 404
    server.abort_on = "/listings/1"  # the slice's first GET (listing 1, the oldest)
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7)

    assert stats.status == "partial"
    assert server.detail_requests() == [5, 1]  # the re-check ran before the slice
    assert (await listings(conn))[5]["active"] is False
    assert stats.deactivated == 1
    assert stats.allowlist_warnings == []  # the guard ran too
    assert stats.failed_listing_ids == [1, 2]  # the slice goes to the next run


async def test_a_still_active_listing_off_the_pages_is_refreshed_by_its_recheck(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    # Listing 3 left every synced category but is still for sale: never in the slice.
    del server.listed[3]
    server.off_list[3] = "active"
    await conn.execute(
        "UPDATE olx_listing SET detail_fetched_at = now() - interval '9 days' WHERE id = 3"
    )
    summary_hash_before = (await listings(conn))[3]["summary_hash"]

    stats = await sync(conn, server, refresh_days=7)

    stored = (await listings(conn))[3]
    assert stored["active"] is True
    assert stored["title"] == "Old listing 3"  # saved from the re-check body
    assert stored["summary_hash"] == summary_hash_before  # no listing-page row this run
    assert stats.detail_refresh_overdue is False
    assert stats.oldest_detail_age_days == pytest.approx(0, abs=0.01)


async def test_a_failed_rolling_id_is_fetched_first_even_when_the_cap_is_full(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    server.detail_errors[1] = 500
    await sync(conn, server, refresh_days=7)  # slice [1, 2]: 1 fails
    del server.detail_errors[1]
    server.listed[9] += " (edited)"
    server.requests.clear()

    stats = await sync(conn, server, refresh_days=7, detail_cap=1)

    assert server.detail_requests() == [1, 9]  # 1 first, though the cap is already full
    assert stats.failed_listing_ids is None
    assert (stats.rolling_refreshed, stats.rolling_deferred) == (0, 2)


async def test_deferred_counts_only_listings_the_slice_could_have_taken(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    for listing_id in range(1, 10):  # 9 of 10 changed: the pool holds only listing 10
        server.listed[listing_id] += " (edited)"

    # The slice wants 2, the cap leaves room for 1, and the pool has only 1: nothing waited.
    stats = await sync(conn, server, refresh_days=7, detail_cap=10)

    assert (stats.rolling_refreshed, stats.rolling_deferred) == (1, 0)


async def test_a_never_fetched_listing_comes_first_in_the_slice(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop(ten_listings())
    await sync(conn, server)
    await age_by_one_day(conn)
    await conn.execute("UPDATE olx_listing SET detail_fetched_at = NULL WHERE id = 7")
    server.requests.clear()

    await sync(conn, server, refresh_days=7)

    assert server.detail_requests() == [7, 1]


async def test_the_refresh_catches_up_after_several_deferred_nights(
    conn: psycopg.AsyncConnection,
) -> None:
    server = FakeShop({i: f"Listing {i}" for i in range(1, 15)})  # 14 listings: 2 a night
    await sync(conn, server)
    for _ in range(9):  # nine nights where changed listings would fill the cap
        await age_by_one_day(conn)
        stats = await sync(conn, server, refresh_days=7, detail_cap=0)
    assert stats.detail_refresh_overdue is True
    assert stats.rolling_deferred == 2

    for _ in range(7):  # then seven normal nights
        await age_by_one_day(conn)
        stats = await sync(conn, server, refresh_days=7)

    assert stats.detail_refresh_overdue is False
    assert stats.oldest_detail_age_days is not None and stats.oldest_detail_age_days < 7


async def test_an_age_query_error_never_fails_a_clean_run(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_conn: psycopg.AsyncConnection) -> float | None:
        raise psycopg.errors.QueryCanceled("statement timeout")

    monkeypatch.setattr(job.store, "oldest_detail_age_days", broken)

    stats = await sync(conn, FakeShop(ten_listings()))

    assert stats.status == "succeeded"
    assert stats.oldest_detail_age_days is None
    assert (await last_run(conn))["status"] == "succeeded"


def test_cli_passes_the_detail_cap_and_refresh_days_settings(
    cli_env: FakeShop, monkeypatch: pytest.MonkeyPatch
) -> None:
    passed: dict[str, Any] = {}
    real_run_sync = cli.run_sync

    async def spy(*args: Any, **kwargs: Any) -> RunStats:
        passed.update(kwargs)
        return await real_run_sync(*args, **kwargs)

    monkeypatch.setattr(cli, "run_sync", spy)
    monkeypatch.setenv("OLX_NIGHTLY_DETAIL_CAP", "500")
    monkeypatch.setenv("OLX_ROLLING_REFRESH_DAYS", "5")

    assert cli.main(["sync"]) == 0

    assert (passed["detail_cap"], passed["refresh_days"]) == (500, 5)


# --- The CLI ----------------------------------------------------------------------------


@pytest.fixture
def cli_env(db: str, monkeypatch: pytest.MonkeyPatch) -> FakeShop:
    server = FakeShop({1: "Panel 83", 2: "Stup 100"})
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setattr(cli, "make_olx_client", lambda settings: make_client(server))
    return server


def cli_runs(database_url: str) -> list[tuple[str, str]]:
    with psycopg.connect(database_url) as connection:
        return connection.execute("SELECT kind, status FROM sync_run ORDER BY id").fetchall()


def test_cli_sync_exits_zero_and_records_a_run(cli_env: FakeShop, db: str) -> None:
    assert cli.main(["sync"]) == 0
    assert cli.main(["sync", "--full"]) == 0
    assert cli_runs(db) == [("nightly", "succeeded"), ("full", "succeeded")]


def test_cli_sync_exits_two_when_olx_stops_the_run_early(cli_env: FakeShop, db: str) -> None:
    cli_env.abort_on = "/users/martom"

    assert cli.main(["sync"]) == 2
    assert cli_runs(db) == [("nightly", "partial")]


def test_cli_sync_exits_one_when_olx_fails(cli_env: FakeShop, db: str) -> None:
    cli_env.errors_on = {"/search": 403}  # not retryable: the category sync can't go on

    assert cli.main(["sync"]) == 1
    assert cli_runs(db) == [("nightly", "failed")]


def test_cli_scheduled_flag_is_recorded(cli_env: FakeShop, db: str) -> None:
    assert cli.main(["sync", "--scheduled"]) == 0
    assert cli.main(["sync"]) == 0

    with psycopg.connect(db) as connection:
        rows = connection.execute("SELECT scheduled FROM sync_run ORDER BY id").fetchall()
    assert rows == [(True,), (False,)]


def test_cli_scheduled_is_not_allowed_with_apply_skipped(cli_env: FakeShop) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["sync", "--scheduled", "--apply-skipped", "1"])
    assert exit_info.value.code == 2


def test_cli_passes_the_interval_setting_and_the_3_hour_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passed: dict[str, Any] = {}
    monkeypatch.setattr(cli, "OlxClient", lambda **kwargs: passed.update(kwargs))
    monkeypatch.setenv("OLX_REQUEST_INTERVAL", "3.5")

    cli.make_olx_client(cli.Settings())

    assert passed["min_request_interval_seconds"] == 3.5
    assert passed["run_deadline_seconds"] == 3 * 60 * 60


def test_cli_apply_skipped_exits_non_zero_for_an_unknown_run(cli_env: FakeShop, db: str) -> None:
    assert cli.main(["sync", "--apply-skipped", "12345"]) == 1
    assert cli_runs(db) == [("apply_skipped", "failed")]


def test_cli_sync_exits_two_for_a_partial_run(cli_env: FakeShop, db: str) -> None:
    cli_env.hidden = 1  # coverage < 100%

    assert cli.main(["sync"]) == 2
    assert cli_runs(db) == [("nightly", "partial")]
