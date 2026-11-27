"""The sync job behind `martom sync` (M1-4): OLX -> `olx_listing`, one `sync_run` per run.

One run (kind `nightly`, or `full`, a manual pass that re-fetches everything), in order:

1. Insert a `sync_run` row ('running').
2. Category sync (`olx.sync.sync_shop`): every listing-page row of the shop, plus coverage.
3. Detail fetch (`olx.detail.fetch_changed_details`): GET /listings/{id} for listings whose
   summary hash is new or changed, plus last run's failed or carried-over ids (never
   deferred). A `full` run fetches every listing instead, so an edit to only the
   description is picked up too.
4. Save every seen row, then the fetched details in batches of `DETAIL_BATCH_SIZE` as
   they arrive. A seen listing that was inactive is reactivated. A failed detail keeps
   the old summary hash, so it is retried next run.
5. Deactivation, only when all of these hold (CLAUDE.md and Tomislav's decisions on #8):
   - the run covered 100% of the shop's reported total (`deactivation_candidates`);
   - the per-listing re-check (`recheck_listings`): GET /listings/{id} answers 404, a
     status other than "active", or another owner. Any error (network, timeout, 429,
     5xx, odd body) never counts as removed: the listing stays active this run;
   - the confirmed ones are at most 10% of the active listings (`deactivation_allowed`).
     Above that the run deactivates none, logs an ERROR and records the skip, with the
     ids, in `sync_run` (`deactivation_skipped`, `skipped_listing_ids`).
6. Allowlist guard (#45, `guard_allowlist`): every listing in `olx/allowlist.toml` must
   have been returned by the category sync this run, in any category ("synced"). One
   that wasn't gets one GET /listings/{id}. Each problem is logged as a warning and
   recorded in `sync_run.allowlist_warnings`; it never changes the status and
   deactivates nothing.
7. Rolling refresh (#135, nightly runs only, `_rolling_refresh`), last on purpose: an
   OLX stop inside it can't skip steps 5 and 6. GET /listings/{id} for the seen listings
   whose detail was fetched longest ago (never fetched first), about 1/7 of the active
   ones (`rolling_slice_size`: ceil(active / ROLLING_REFRESH_DAYS)), so every listing is
   re-fetched within 7 nights and a description-only edit is caught without a weekly
   full run. A nightly run sends at most NIGHTLY_REQUEST_BUDGET (950) OLX requests in
   all (OLX allows 1,000 per fixed hourly window, #105): list pages, profile, allowlist
   and re-check GETs and the details of step 3 always go through, even past the budget
   (logged as `request_budget_exceeded`); the slice gets only what is left and is
   trimmed or deferred (`rolling_deferred`; it doesn't change the status). Its GETs
   follow the same rules as step 3 (failures go to `failed_listing_ids`; a detail GET
   never deactivates). The budget is checked before each slice GET, so only the retries
   of the one GET in flight (at most 4) can push past it. An active listing that isn't
   on the listing pages can't be in the slice; the step 5 re-check saves its detail when
   OLX says it is still active, so it stays fresh too.
8. Write the stats and the outcome to the `sync_run` row: 'succeeded' for a clean run,
   'partial' below 100% coverage or when a GET still failed after its retries (those ids
   go in `failed_listing_ids`, and the next run fetches and re-checks them first),
   'skipped_guard' when a guard skipped deactivation (`run_outcome`), 'failed' when the
   run broke. GETs that succeeded on a retry only add to `retried_count`; the request
   interval the run ended with goes in `final_request_interval`. The age of the oldest
   detail among the active listings goes in `oldest_detail_age_days`; over
   MAX_DETAIL_AGE_DAYS (8) it logs a WARNING and sets `detail_refresh_overdue` for the
   alert (M6-3), without changing the status. Also recorded when OLX stopped or failed
   the run, since that is when the refresh falls behind.

Getting a real large removal past the 10% guard:

- **Automatic:** when a scheduled run (`--scheduled`) trips the guard and the scheduled
  run before it (nightly or full) tripped it too, at least 12 h earlier, this second run
  deactivates the ids flagged in both runs that its own re-check confirms, without the
  10% guard (Tomislav, 26 Nov; R1-8). Runs started by hand never auto-apply and don't
  break the streak (`store.previous_skipped_run`). If the
  re-check fails for more than 20% of those ids, it applies none of them and flags the
  run (`recheck_skipped`, status 'skipped_guard'). The other confirmed ids stay skipped
  and are stored in `skipped_listing_ids`, so the next run can match them.
- **Manual:** `martom sync --apply-skipped <run id>` (`apply_skipped_run`) re-checks the
  ids stored for that skipped run and deactivates the confirmed ones, with the same
  per-listing rules and the same 20% re-check guard. It is its own `sync_run`
  (kind `apply_skipped`).

When OLX stops the run early (`OlxRateLimitAbortError`: a Retry-After over the 1-hour
ceiling, or `OlxRunDeadlineError`: waiting would pass the 3-hour run deadline), the run
ends at once as 'partial' (CLI exit 2): what was fetched before it is saved, nothing is
deactivated, and the ids it didn't get to (details still to fetch, candidates still to
re-check, and last run's failed ids not yet retried) go in `failed_listing_ids`, so the
next run takes them first. They don't count as `errors`: no GET failed for them. Any other
OLX failure that stops a step ends the run as 'failed' (CLI exit 1); the next scheduled
run tries again.
"""

import itertools
import logging
import math
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import httpx
import psycopg

from martom.olx import store
from martom.olx.allowlist import Allowlist, AllowlistWarning, check_seen, load_allowlist
from martom.olx.client import OlxApiError, OlxClient, OlxCounters, OlxRateLimitAbortError
from martom.olx.detail import (
    DetailFetchAbortedError,
    DetailFetchResult,
    ListingDetail,
    fetch_changed_details,
    needs_detail_fetch,
)
from martom.olx.sync import OlxResponseError, SyncResult, deactivation_candidates, sync_shop

logger = logging.getLogger(__name__)

RunKind = Literal["nightly", "full", "apply_skipped"]
RunStatus = Literal["succeeded", "partial", "skipped_guard", "failed"]
GoneReason = Literal["404", "inactive", "not_martom"]

# At most this share of the active listings may be deactivated in one run (10% guard).
MAX_DEACTIVATION_PERCENT = 10
# Applying skipped ids is called off when the re-check fails for more than this share.
MAX_RECHECK_FAILURE_PERCENT = 20
# Fetched details are saved every this many listings (about 7 minutes at 2.0 s each).
DETAIL_BATCH_SIZE = 200
# Rolling refresh (#135, Tomislav 27 Nov): a nightly run sends at most this many OLX
# requests of every kind (OLX allows 1,000 per fixed hourly window, #105; ~44 min at
# 2.8 s), and every active listing is re-fetched within this many nights. Only the
# rolling slice gives way to the budget. `martom sync` reads both from Settings
# (OLX_NIGHTLY_REQUEST_BUDGET, OLX_ROLLING_REFRESH_DAYS), whose defaults are these.
NIGHTLY_REQUEST_BUDGET = 950
ROLLING_REFRESH_DAYS = 7
# A detail older than this (days) means the rolling refresh fell behind: alert (M6-3).
MAX_DETAIL_AGE_DAYS = 8
# The overdue WARNING names at most this many listing ids, oldest first.
OVERDUE_IDS_LOGGED = 20


@dataclass
class RunStats:
    """What one run did; the fields match the `sync_run` columns of the same name."""

    kind: RunKind
    scheduled: bool = False  # written by `store.start_run`
    status: RunStatus = "failed"
    shop_total: int | None = None
    coverage: float | None = None
    seen: int = 0
    new: int = 0
    fetched: int = 0
    changed: int = 0
    deactivation_candidates: int = 0
    deactivated: int = 0
    reactivated: int = 0
    errors: int = 0  # GETs that still failed after their retries
    retried_count: int = 0  # GETs that succeeded only after a retry
    failed_listing_ids: list[int] | None = None
    final_request_interval: float | None = None
    deactivation_skipped: bool = False
    would_deactivate: int | None = None
    skipped_listing_ids: list[int] | None = None
    applied_run_ids: list[int] | None = None
    recheck_skipped: bool = False
    # None: the guard didn't run; []: it ran and found nothing (see migration 008).
    allowlist_warnings: list[dict[str, Any]] | None = None
    rolling_refreshed: int = 0  # rolling-slice detail GETs that succeeded
    rolling_deferred: int = 0  # rolling-slice listings the request budget left for later
    oldest_detail_age_days: float | None = None
    detail_refresh_overdue: bool = False
    error: str | None = None
    run_id: int | None = field(default=None, compare=False)

    def columns(self) -> dict[str, object]:
        """The values for `store.finish_run` (all but `kind`, `scheduled` and `run_id`)."""
        values = asdict(self)  # dataclass -> dict, like `{...stats}` in JS
        del values["kind"], values["scheduled"], values["run_id"]
        return values


def rolling_slice_size(active_count: int, *, refresh_days: int) -> int:
    """How many listings the rolling refresh re-fetches per night: ceil(active / days).

    Rounded up, so `refresh_days` nights always cover every active listing.
    """
    return math.ceil(active_count / refresh_days)


def deactivation_allowed(would_deactivate: int, active_count: int) -> bool:
    """True when `would_deactivate` is at most 10% of `active_count` (exactly 10% is fine).

    Integer math, so 1 of 10 is not lost to float rounding.
    """
    return would_deactivate * 100 <= active_count * MAX_DEACTIVATION_PERCENT


def run_outcome(stats: "RunStats") -> RunStatus:
    """The status of a run that didn't break (see the `sync_run.status` comment in 002).

    Precedence: skipped_guard > partial > succeeded ('failed' is set by the caller when
    the run breaks). Partial: coverage below 100%, or any detail fetch or re-check that
    still failed after its retries (`errors`). A GET that succeeded on a retry only adds
    to `retried_count`. `coverage` is None for `--apply-skipped`, which pages nothing.
    """
    if stats.deactivation_skipped or stats.recheck_skipped:
        return "skipped_guard"
    if stats.coverage is not None and stats.coverage < 1:
        return "partial"
    if stats.errors > 0:
        return "partial"
    return "succeeded"


def _failed_first(ids: Iterable[int], failed: Collection[int]) -> list[int]:
    """`ids` with the ones that failed last run first, both parts in their original order."""
    ordered = list(ids)
    return [i for i in ordered if i in failed] + [i for i in ordered if i not in failed]


def _record_failures(stats: "RunStats", listing_ids: Iterable[int]) -> None:
    """Add ids whose GET still failed after its retries to the run's failed list and count."""
    new = set(listing_ids)
    if not new:
        return
    stats.errors += len(new)
    stats.failed_listing_ids = sorted(set(stats.failed_listing_ids or ()) | new)


def _carry_over(stats: "RunStats", listing_ids: Iterable[int]) -> None:
    """Add ids a run stopped early didn't get to; next run takes them first (not errors)."""
    stats.failed_listing_ids = (
        sorted(set(stats.failed_listing_ids or ()) | set(listing_ids)) or None
    )


def recheck_allowed(failures: int, total: int) -> bool:
    """True when re-check failures are at most 20% of the ids re-checked."""
    return failures * 100 <= total * MAX_RECHECK_FAILURE_PERCENT


# --- Per-listing re-check -----------------------------------------------------------------


@dataclass
class Recheck:
    """What GET /listings/{id} said about each id."""

    gone: dict[int, GoneReason] = field(default_factory=dict)
    still_active: set[int] = field(default_factory=set)
    errors: dict[int, str] = field(default_factory=dict)  # id -> error type
    # The parsed GET body of each still-active listing, saved as its fresh detail (R1-2).
    details: dict[int, ListingDetail] = field(default_factory=dict)


def _error_type(exc: OlxApiError) -> str:
    """A short label for the log: "timeout", "network", "429", "5xx", "http_403", ..."""
    if exc.status_code is None:
        return "timeout" if isinstance(exc.__cause__, httpx.TimeoutException) else "network"
    if exc.status_code >= 500:
        return "5xx"
    if exc.status_code == 429:
        return "429"
    return f"http_{exc.status_code}"


def _as_int(value: object) -> int | None:
    """`int(value)` for 17193 or "17193"; None for anything else (bools excluded)."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


async def shop_id(olx: OlxClient) -> int:
    """The shop's numeric OLX user id (to recognise listings that changed owner)."""
    profile = await olx.get_shop()
    try:
        return int(profile["data"]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OlxResponseError(f"unexpected OLX response: shop profile ({exc!r})") from exc


async def recheck_listings(
    olx: OlxClient,
    listing_ids: Collection[int],
    owner_id: int,
    *,
    first: Collection[int] = (),
) -> Recheck:
    """GET /listings/{id} for each id (through the rate-limited client) and sort the answers.

    Ids in `first` (those whose re-check failed last run) are asked about first; the rest
    follow in id order.

    Gone: 404, a `status` other than "active" ("inactive"), or a `user.id` other than
    `owner_id` ("not_martom"). Still active: status "active" and Martom's. Everything
    else is an error and never counts as removed. Each id is logged with its outcome.
    `OlxRateLimitAbortError` propagates: the caller ends the run and deactivates nothing.
    """
    result = Recheck()
    for listing_id in _failed_first(sorted(listing_ids), first):
        try:
            body = await olx.get_listing(listing_id)
        except OlxRateLimitAbortError:
            raise
        except OlxApiError as exc:
            if exc.status_code == 404:
                result.gone[listing_id] = "404"
            else:
                result.errors[listing_id] = _error_type(exc)
                logger.warning(
                    "listing %d: re-check failed (%s), stays active: %s",
                    listing_id,
                    result.errors[listing_id],
                    exc,
                )
            continue
        # OLX mixes number and string ids (`top_category_id` comes as "701"), so compare
        # them as ints, never as raw JSON.
        if not isinstance(body, Mapping) or _as_int(body.get("id")) != listing_id:
            result.errors[listing_id] = "unexpected_body"
            logger.warning("listing %d: re-check got an unexpected body, stays active", listing_id)
            continue
        user = body.get("user")
        owner = _as_int(user.get("id")) if isinstance(user, Mapping) else None
        status = body.get("status")
        if owner is not None and owner != owner_id:
            result.gone[listing_id] = "not_martom"
        elif isinstance(status, str) and status != "active":
            result.gone[listing_id] = "inactive"
        elif status == "active":
            result.still_active.add(listing_id)
            logger.info("listing %d: not on the listing pages but still active on OLX", listing_id)
            try:
                result.details[listing_id] = ListingDetail.from_api(body)
            except OlxResponseError as exc:  # still active; its detail just isn't saved
                logger.warning("listing %d: re-check body not saved as detail: %s", listing_id, exc)
        else:
            result.errors[listing_id] = "unexpected_body"
            logger.warning("listing %d: re-check got no status, stays active", listing_id)
    return result


async def _deactivate(
    conn: psycopg.AsyncConnection,
    gone: Mapping[int, GoneReason],
    stats: RunStats,
    *,
    trigger: str,
) -> None:
    """Deactivate `gone`; log each id that actually changed, with its reason and trigger.

    Ids that were already inactive (say, `--apply-skipped` run twice) are not logged or
    counted.
    """
    changed = await store.deactivate(conn, gone.keys())
    for listing_id in changed:
        reason = gone[listing_id]
        logger.info(
            "listing %d deactivated: %s (%s)",
            listing_id,
            reason,
            trigger,
            extra={"event": "listing_deactivated", "listing_id": listing_id, "reason": reason},
        )
    stats.deactivated += len(changed)


def _flag_recheck_failure(stats: RunStats, recheck: Recheck, ids: Collection[int]) -> None:
    failures = sorted(set(recheck.errors) & set(ids))
    stats.recheck_skipped = True
    logger.error(
        "re-check failed for %d of %d skipped listings (over %d%%): applying none of them",
        len(failures),
        len(ids),
        MAX_RECHECK_FAILURE_PERCENT,
        extra={
            "event": "recheck_skipped",
            "run_id": stats.run_id,
            "failed_ids": failures,
            "rechecked": len(ids),
        },
    )


# --- The runs -----------------------------------------------------------------------------


async def run_sync(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    *,
    kind: RunKind,
    scheduled: bool = False,
    now: Callable[[], datetime] | None = None,
    allowlist: Allowlist | None = None,
    request_budget: int = NIGHTLY_REQUEST_BUDGET,
    refresh_days: int | None = ROLLING_REFRESH_DAYS,
) -> RunStats:
    """Run one sync (`nightly` or `full`) and record it in `sync_run`; return its stats.

    `scheduled`: started by the scheduler, so it may auto-apply (module docstring).
    `now` stamps `sync_run.started_at` (default: the database's now()); tests pass a fake.
    `allowlist`: the listings the guard checks (default: the committed `allowlist.toml`).
    `request_budget` and `refresh_days`: the nightly rolling refresh (step 7);
    `refresh_days` None turns it off. A `full` run ignores both: it fetches every
    listing, with no budget.

    OLX failures don't raise: they end the run as 'partial' (OLX stopped it early) or
    'failed' (see the module docstring). Anything else (a database error, a bug) is
    recorded as 'failed' when the database still allows it, then re-raised.
    """
    if kind == "apply_skipped":
        raise ValueError("use apply_skipped_run for kind 'apply_skipped'")
    # Read before the run starts: a broken file stops here, not halfway through a sync.
    checked = load_allowlist() if allowlist is None else allowlist
    return await _recorded(
        conn,
        olx,
        kind,
        lambda stats: _sync(
            conn, olx, stats, checked, request_budget=request_budget, refresh_days=refresh_days
        ),
        scheduled=scheduled,
        started_at=None if now is None else now(),
    )


async def apply_skipped_run(
    conn: psycopg.AsyncConnection, olx: OlxClient, skipped_run_id: int
) -> RunStats:
    """`martom sync --apply-skipped <run id>`: apply the ids a guard-skipped run stored.

    Only those ids, never a fresh list. Each is re-checked with GET /listings/{id}; only
    the confirmed ones are deactivated. More than 20% re-check failures apply nothing and
    end the run as 'skipped_guard', so it can be tried again later. If OLX stops the run
    during the re-check, nothing is applied and the run is 'partial'; run it again.
    """

    async def apply(stats: RunStats) -> None:
        stats.applied_run_ids = [skipped_run_id]
        ids = await store.load_skipped_ids(conn, skipped_run_id)
        if not ids:
            raise _RunFailedError(f"sync run {skipped_run_id} has no skipped deactivations")
        stats.deactivation_candidates = len(ids)
        recheck = await recheck_listings(olx, ids, await shop_id(olx))
        _record_failures(stats, recheck.errors)
        if not recheck_allowed(len(recheck.errors), len(ids)):
            _flag_recheck_failure(stats, recheck, ids)  # status becomes skipped_guard
            return
        await _deactivate(conn, recheck.gone, stats, trigger=f"applying run {skipped_run_id}")

    return await _recorded(conn, olx, "apply_skipped", apply)


class _RunFailedError(Exception):
    """Ends a run as 'failed' with this message (not an OLX error)."""


async def _recorded(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    kind: RunKind,
    body: Callable[[RunStats], Awaitable[None]],
    *,
    scheduled: bool = False,
    started_at: datetime | None = None,
) -> RunStats:
    """Open a `sync_run` row, run `body`, and record how it ended."""
    stats = RunStats(kind=kind, scheduled=scheduled)
    stats.run_id = await store.start_run(conn, kind, scheduled=scheduled, started_at=started_at)
    # The client's counters are running totals; this run's numbers are the difference (R1-3).
    at_start = olx.counters()

    def take_client_counters() -> OlxCounters:
        run_counters = olx.counters() - at_start
        stats.retried_count = run_counters.recovered_requests
        stats.final_request_interval = olx.request_interval
        return run_counters

    logger.info("sync run %d (%s) started", stats.run_id, kind)
    try:
        await body(stats)
        stats.status = run_outcome(stats)
    except OlxRateLimitAbortError as exc:  # also OlxRunDeadlineError: OLX stopped us early
        stats.status = "partial"
        stats.error = str(exc)
        logger.error(
            "sync run %d (%s) stopped early: %s; %d listing ids left for the next run",
            stats.run_id,
            kind,
            exc,
            len(stats.failed_listing_ids or ()),
        )
    except (OlxApiError, _RunFailedError) as exc:
        stats.status = "failed"
        stats.error = str(exc)
        logger.error("sync run %d (%s) failed: %s", stats.run_id, kind, exc)
    except Exception as exc:
        stats.status = "failed"
        stats.error = f"{type(exc).__name__}: {exc}"
        # Log first: the summary is the only record of the OLX numbers, and it must not
        # depend on the database still working.
        _log_summary(stats, take_client_counters())
        await _finish_quietly(conn, stats)
        raise
    run_counters = take_client_counters()
    await store.finish_run(conn, stats.run_id, stats.columns())
    _log_summary(stats, run_counters)
    return stats


async def _sync(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    stats: RunStats,
    allowlist: Allowlist,
    *,
    request_budget: int,
    refresh_days: int | None,
) -> None:
    """One nightly/full run; when it stops early, carry over the ids still to do.

    That holds for every way it can stop (OLX stopping it -> 'partial', or any other
    error -> 'failed'), so the run after it still takes those ids first. The oldest
    detail age is recorded after a run that finished and after one OLX stopped or failed
    (the database still works then); not after a database error or a bug.
    """
    assert stats.run_id is not None  # set by `_recorded` before the run starts
    # Listings whose GET still failed last run go first, so a short run retries them.
    failed_last_run = await store.previous_failed_ids(conn, stats.run_id)
    # Ids that still need a GET this run (detail fetch or re-check), kept up to date as
    # the run goes, for the case that it stops early: those go to the next run.
    todo = set(failed_last_run)
    rolling = (request_budget, refresh_days) if stats.kind == "nightly" and refresh_days else None
    try:
        await _sync_steps(conn, olx, stats, failed_last_run, todo, allowlist, rolling)
    except (OlxApiError, _RunFailedError):  # OLX stopped or failed the run
        _carry_over(stats, todo)
        await _record_detail_age(conn, stats)
        raise
    except Exception:
        _carry_over(stats, todo)
        raise
    await _record_detail_age(conn, stats)


async def _record_detail_age(conn: psycopg.AsyncConnection, stats: RunStats) -> None:
    """Record the oldest detail age among the active listings; WARNING when overdue.

    The WARNING names the overdue listings by id (at most `OVERDUE_IDS_LOGGED`). A
    database error here is logged and swallowed: a metric never changes the run's
    status, and never hides the error a failing run is ending with.
    """
    try:
        age = await store.oldest_detail_age_days(conn)
        overdue = age is not None and age > MAX_DETAIL_AGE_DAYS
        ids = (
            await store.overdue_detail_ids(conn, MAX_DETAIL_AGE_DAYS, limit=OVERDUE_IDS_LOGGED)
            if overdue
            else []
        )
    except psycopg.Error:
        logger.exception("could not record the oldest detail age of run %s", stats.run_id)
        return
    stats.oldest_detail_age_days = age
    stats.detail_refresh_overdue = overdue
    if overdue:
        logger.warning(
            "rolling refresh behind: the oldest active listing detail is %.1f days old "
            "(over %d); oldest listings: %s",
            age,
            MAX_DETAIL_AGE_DAYS,
            ", ".join(str(i) for i in ids),
            extra={
                "event": "detail_refresh_overdue",
                "run_id": stats.run_id,
                "oldest_detail_age_days": age,
                "overdue_listing_ids": ids,
            },
        )


async def _sync_steps(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    stats: RunStats,
    failed_last_run: set[int],
    todo: set[int],
    allowlist: Allowlist,
    rolling: tuple[int, int] | None,
) -> None:
    """The steps of one run. `rolling`: (request budget, refresh days), None for no slice."""
    assert stats.run_id is not None
    # The client's counter is a running total (it may be reused); count from here.
    sent_at_start = olx.requests_sent
    result = await sync_shop(olx)
    stats.shop_total = result.shop_total
    stats.coverage = result.coverage
    stats.seen = result.unique_count

    stored = await store.load_listings(conn)
    # A full run pretends no hash is stored, so every listing is fetched again. A nightly
    # run leaves out the hashes of last run's failed or carried-over ids too, so they are
    # fetched first whatever their summary says (a failed rolling GET has an unchanged
    # summary); they are never deferred by the request budget.
    stored_hashes = (
        {}
        if stats.kind == "full"
        else {
            i: s.summary_hash
            for i, s in stored.items()
            if s.summary_hash is not None and i not in failed_last_run
        }
    )
    # Summaries first, then details batch by batch, so a crash or a dropped connection
    # an hour into a full run loses one batch, not every detail fetched so far.
    await store.save_summaries(conn, result.listings)
    stats.new = sum(1 for i in result.listings if i not in stored)
    stats.reactivated = sum(1 for i in result.listings if i in stored and not stored[i].active)
    # New, changed, or failed last run: always fetched, never trimmed by the request budget.
    due = {i for i, row in result.listings.items() if needs_detail_fetch(row, stored_hashes.get(i))}
    todo.update(due)
    for batch in itertools.batched(
        _failed_first(result.listings, failed_last_run), DETAIL_BATCH_SIZE
    ):
        rows = {i: result.listings[i] for i in batch}
        try:
            details = await fetch_changed_details(olx, rows, stored_hashes)
        except DetailFetchAbortedError as exc:
            await _save_details(conn, exc.partial, stored, stats, todo)
            raise
        await _save_details(conn, details, stored, stats, todo)

    warnings = await guard_allowlist(olx, allowlist, result.listings)
    stats.allowlist_warnings = [warning.as_json() for warning in warnings]

    await _deactivation_step(conn, olx, stats, stored, result, failed_last_run, todo)

    # Last (R1-1): the rolling slice is upkeep, so an OLX stop inside it (up to ~1,000
    # GETs) must not cost the allowlist guard or the deactivation re-check above.
    if rolling is not None:
        request_budget, refresh_days = rolling
        await _rolling_refresh(
            conn,
            olx,
            stats,
            result.listings,
            due,
            stored,
            todo,
            sent_this_run=lambda: olx.requests_sent - sent_at_start,
            request_budget=request_budget,
            refresh_days=refresh_days,
        )


async def _deactivation_step(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    stats: RunStats,
    stored: Mapping[int, store.StoredListing],
    result: SyncResult,
    failed_last_run: set[int],
    todo: set[int],
) -> None:
    """Step 5: re-check the active listings the run didn't see; deactivate the gone ones.

    A candidate OLX says is still active gets its detail saved from the re-check body
    (`store.save_rechecked_details`): that GET is a real detail fetch, and without it a
    listing that left every synced category would never be refreshed (R1-2).
    """
    assert stats.run_id is not None
    candidates = deactivation_candidates((i for i, s in stored.items() if s.active), result)
    stats.deactivation_candidates = len(candidates)
    if not candidates:
        return
    # All of them until the re-check is done: an abort midway loses its answers so far.
    todo.update(candidates)
    recheck = await recheck_listings(olx, candidates, await shop_id(olx), first=failed_last_run)
    todo.difference_update(candidates)
    _record_failures(stats, recheck.errors)
    await store.save_rechecked_details(conn, recheck.details)
    active_count = await store.count_active(conn)  # before any deactivation below
    gone: dict[int, GoneReason] = dict(recheck.gone)
    if not gone:
        return
    if deactivation_allowed(len(gone), active_count):
        await _deactivate(conn, gone, stats, trigger=f"sync run {stats.run_id}")
        return

    # The 10% guard trips: deactivate none of them, except (auto-apply) the ids the
    # previous run flagged as well, when that run tripped the guard too.
    stats.deactivation_skipped = True
    stats.would_deactivate = len(gone)
    logger.error(
        "deactivation skipped: %d of %d active listings (over %d%%) would be deactivated",
        len(gone),
        active_count,
        MAX_DEACTIVATION_PERCENT,
        extra={
            "event": "deactivation_skipped",
            "run_id": stats.run_id,
            "would_deactivate": len(gone),
            "active_listings": active_count,
        },
    )
    # Only a scheduled run may auto-apply (R1-8); one started by hand uses --apply-skipped.
    previous = await store.previous_skipped_run(conn, stats.run_id) if stats.scheduled else None
    if previous is not None:
        previous_id, flagged = previous
        # Flagged in both runs; re-check failures among them count against the 20% guard.
        both = flagged & candidates
        if both and not recheck_allowed(len(both & recheck.errors.keys()), len(both)):
            _flag_recheck_failure(stats, recheck, both)
        elif both:
            auto_gone: dict[int, GoneReason] = {i: gone.pop(i) for i in sorted(both) if i in gone}
            trigger_runs = [stats.run_id, previous_id]
            logger.warning(
                "auto-apply: deactivating %d listings flagged by both skipped runs %d and %d",
                len(auto_gone),
                previous_id,
                stats.run_id,
                extra={"event": "auto_apply", "run_id": stats.run_id, "trigger_runs": trigger_runs},
            )
            stats.applied_run_ids = trigger_runs
            trigger = f"auto-apply of runs {previous_id} and {stats.run_id}"
            await _deactivate(conn, auto_gone, stats, trigger=trigger)
    # What is still skipped: the next run (or `--apply-skipped`) works from these ids.
    # None when the auto-apply took every confirmed id. The run stays 'skipped_guard'
    # on purpose: the guard tripped and a mass deactivation went past it, which
    # someone should look at.
    stats.skipped_listing_ids = sorted(gone) or None


async def _rolling_refresh(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    stats: RunStats,
    seen: Mapping[int, Mapping[str, Any]],
    due: Collection[int],
    stored: Mapping[int, store.StoredListing],
    todo: set[int],
    *,
    sent_this_run: Callable[[], int],
    request_budget: int,
    refresh_days: int,
) -> None:
    """Step 7, the run's last: re-fetch the details fetched longest ago (#135).

    Only listings seen this run (a detail is saved with the summary hash of the row it
    belongs to) and not already fetched in step 3 (`due`). The slice is
    ceil(active / days), trimmed to what the run's earlier requests of every kind left of
    `request_budget`. The budget is checked again before each slice GET: once the run has
    sent `request_budget` requests no new GET starts, so a retry or a 429 inside the slice
    shrinks the rest of it, and the run ends at most `budget + (max_attempts - 1)` (4 by
    default: the retries of the one GET in flight) when the slice starts under the
    budget. The listings left out are `rolling_deferred` (never more than the pool had)
    and are not carried over as failed.
    """
    wanted = rolling_slice_size(await store.count_active(conn), refresh_days=refresh_days)
    pool = set(seen) - set(due)
    must_do = sent_this_run()
    if must_do > request_budget:
        logger.warning(
            "request budget exceeded before the rolling refresh: %d requests sent, budget "
            "%d; nothing was deferred but the rolling slice",
            must_do,
            request_budget,
            extra={
                "event": "request_budget_exceeded",
                "run_id": stats.run_id,
                "requests_sent": must_do,
                "request_budget": request_budget,
            },
        )
    ids = await store.oldest_detail_ids(
        conn, pool, limit=min(wanted, max(0, request_budget - must_do))
    )
    attempted = 0  # slice GETs started (fetched or failed)
    for batch in itertools.batched(ids, DETAIL_BATCH_SIZE):
        todo.update(batch)
        rows = {i: seen[i] for i in batch}  # oldest first: dicts keep insertion order
        try:
            # No stored hashes: every listing in `rows` is fetched, unchanged or not. No new
            # GET starts once the run has sent `request_budget` requests.
            details = await fetch_changed_details(
                olx, rows, {}, stop=lambda: sent_this_run() >= request_budget
            )
        except DetailFetchAbortedError as exc:
            stats.rolling_refreshed += len(exc.partial.fetched)
            await _save_details(conn, exc.partial, stored, stats, todo)
            raise
        stats.rolling_refreshed += len(details.fetched)
        attempted += len(details.fetched) + len(details.failed)
        await _save_details(conn, details, stored, stats, todo)
        if details.not_started:
            todo.difference_update(details.not_started)  # deferred, not carried over
            break
    stats.rolling_deferred = min(wanted, len(pool)) - attempted
    logger.info(
        "rolling refresh: %d listings re-fetched, %d deferred (%d requests before it, budget %d)",
        attempted,
        stats.rolling_deferred,
        must_do,
        request_budget,
        extra={
            "event": "rolling_refresh",
            "run_id": stats.run_id,
            "rolling": attempted,
            "rolling_deferred": stats.rolling_deferred,
            "requests_before": must_do,
            "request_budget": request_budget,
        },
    )


async def guard_allowlist(
    olx: OlxClient, allowlist: Allowlist, seen: Mapping[int, Mapping[str, Any]]
) -> list[AllowlistWarning]:
    """Check the allowlisted listings against what the category sync saw (#45).

    A listing the sync returned is synced in whatever category (`check_seen` only logs a
    move at INFO; a row without a category_id is "recheck_failed"). A listing it didn't
    return gets one GET /listings/{id} through the rate-limited client (the same
    re-check as deactivation candidates): 404, "inactive" or "not_martom" when OLX says
    it is gone, "not_seen" when it is still active (it left every category the sync
    pages), "recheck_failed" when the GET failed. Every warning is logged; none raises, so the run
    goes on. Only `OlxRateLimitAbortError` propagates: OLX stopped the whole run.
    """
    warnings, missing = check_seen(allowlist, seen)
    if missing:
        try:
            recheck = await recheck_listings(olx, missing, await shop_id(olx))
        except OlxRateLimitAbortError:
            raise
        except OlxApiError as exc:  # the shop profile failed: no GET was made
            recheck = Recheck(errors=dict.fromkeys(missing, _error_type(exc)))
        for entry in allowlist.listings:
            if entry.id not in missing:
                continue
            if entry.id in recheck.gone:
                reason, error = recheck.gone[entry.id], None
            elif entry.id in recheck.still_active:
                reason, error = "not_seen", None
            else:
                reason, error = "recheck_failed", recheck.errors.get(entry.id, "unknown")
            warnings.append(
                AllowlistWarning(
                    listing_id=entry.id,
                    name=entry.name,
                    reason=reason,
                    expected_category_id=entry.category_id,
                    error=error,
                )
            )
    order = [entry.id for entry in allowlist.listings]
    warnings.sort(key=lambda warning: order.index(warning.listing_id))
    for warning in warnings:
        logger.warning(
            "allowlisted listing %d (%s): %s; drop it from olx/allowlist.toml if that is expected",
            warning.listing_id,
            warning.name,
            _describe(warning),
            # `name` is a reserved LogRecord attribute, so it goes in as `listing_name`.
            extra={
                "event": "allowlist_warning",
                "listing_id": warning.listing_id,
                "listing_name": warning.name,
                "reason": warning.reason,
                "expected_category_id": warning.expected_category_id,
                "category_id": warning.category_id,
                "error": warning.error,
            },
        )
    return warnings


def _describe(warning: AllowlistWarning) -> str:
    match warning.reason:
        case "not_seen":
            return "not on the listing pages, though OLX says it is still active"
        case "recheck_failed" if warning.error == "no_category_id":
            return "on the listing pages, but the row has no category_id (not checked)"
        case "recheck_failed":
            return f"not on the listing pages, and GET /listings failed ({warning.error})"
        case _:  # 404, inactive, not_martom
            return f"not on the listing pages, and OLX says it is gone ({warning.reason})"


async def _save_details(
    conn: psycopg.AsyncConnection,
    details: DetailFetchResult,
    stored: Mapping[int, store.StoredListing],
    stats: RunStats,
    todo: set[int],
) -> None:
    """Save one batch of fetched details and add it to the fetched / changed / error counts.

    Every id the batch answered for (fetched, unchanged or failed) leaves `todo`.
    """
    await store.save_details(conn, details.fetched)
    todo.difference_update(details.fetched, details.unchanged, details.failed)
    stats.fetched += len(details.fetched)
    _record_failures(stats, details.failed)
    stats.changed += sum(
        1
        for i, fetched in details.fetched.items()
        if i in stored and stored[i].detail_hash != fetched.detail.detail_hash
    )


async def _finish_quietly(conn: psycopg.AsyncConnection, stats: RunStats) -> None:
    """Best effort 'failed' record on an unexpected error; never hides the original one."""
    if stats.run_id is None or conn.broken:
        return
    try:
        await store.finish_run(conn, stats.run_id, stats.columns())
    except psycopg.Error:
        logger.exception("could not record sync run %d as failed", stats.run_id)


def _log_summary(stats: RunStats, olx: OlxCounters) -> None:
    """One line per run, also for a run that crashed. Besides the `sync_run` columns it
    carries the OLX timing (requests, 429s, the wait OLX asked for, elapsed) that #105
    needs and that has no column. `olx_429_requested_wait_seconds` is the sum of the 429
    waits OLX asked for (Retry-After, or our backoff), not time actually slept.

    `olx` holds this run's numbers only (counters at the end minus at the start), so a
    client reused across runs still reports each run on its own.
    """
    timing = {
        "olx_requests_sent": olx.requests_sent,
        "olx_429_count": olx.rate_limited,
        "olx_429_requested_wait_seconds": olx.requested_wait_seconds,
        "olx_elapsed_seconds": olx.elapsed_seconds,
    }
    log = logger.info if stats.status == "succeeded" else logger.error
    log(
        "sync run %s (%s) %s: seen %d, new %d, deactivated %d, reactivated %d, "
        "fetched %d, changed %d, errors %d, coverage %s; "
        "OLX %d requests in %.0f s, %d x 429, OLX asked to wait %.0f s",
        stats.run_id,
        stats.kind,
        stats.status,
        stats.seen,
        stats.new,
        stats.deactivated,
        stats.reactivated,
        stats.fetched,
        stats.changed,
        stats.errors,
        "n/a" if stats.coverage is None else f"{stats.coverage:.2%}",
        olx.requests_sent,
        olx.elapsed_seconds,
        olx.rate_limited,
        olx.requested_wait_seconds,
        extra={
            "event": "sync_run",
            **stats.columns(),
            **timing,
            "run_id": stats.run_id,
            "kind": stats.kind,
        },
    )


__all__ = [
    "GoneReason",
    "Recheck",
    "RunKind",
    "RunStats",
    "apply_skipped_run",
    "deactivation_allowed",
    "guard_allowlist",
    "recheck_allowed",
    "recheck_listings",
    "rolling_slice_size",
    "run_outcome",
    "run_sync",
    "shop_id",
]
