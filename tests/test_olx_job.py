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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from martom import cli
from martom.migrate import run_migrations
from martom.olx import job
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

pytestmark = pytest.mark.anyio

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "olx" / "listing_details.json"
SHOP_ID = 17193
LEAF, TOP = 881, 701
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
        self.requests: list[httpx.Request] = []

    def summary_row(self, listing_id: int) -> dict[str, Any]:
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
) -> RunStats:
    """One run; `at` fakes its `started_at` (default: the database's now())."""
    async with make_client(server, max_attempts=max_attempts) as olx:
        return await run_sync(
            conn,
            olx,
            kind="full" if full else "nightly",
            scheduled=scheduled,
            now=None if at is None else (lambda: at),
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
    del server.listed[19], server.listed[20]  # 2 of 20 = 10%: allowed
    server.detail_errors = {20: 503}
    await sync(conn, server)
    assert (await last_run(conn))["failed_listing_ids"] == [20]
    server.detail_errors = {}
    server.requests.clear()

    await sync(conn, server)

    assert server.detail_requests()[0] == 20
    assert (await listings(conn))[20]["active"] is False


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
