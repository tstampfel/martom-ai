"""The sync job behind `martom sync` (M1-4): OLX -> `olx_listing`, one `sync_run` per run.

One run (kind `nightly`, or `full` for the weekly pass), in order:

1. Insert a `sync_run` row ('running').
2. Category sync (`olx.sync.sync_shop`): every listing-page row of the shop, plus coverage.
3. Detail fetch (`olx.detail.fetch_changed_details`): GET /listings/{id} for listings whose
   summary hash is new or changed. A `full` run fetches every listing instead, so an edit
   to only the description is picked up too.
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
6. Write the stats and the outcome to the `sync_run` row: 'succeeded' for a clean run,
   'partial' below 100% coverage or when a GET still failed after its retries (those ids
   go in `failed_listing_ids`, and the next run fetches and re-checks them first),
   'skipped_guard' when a guard skipped deactivation (`run_outcome`), 'failed' when the
   run broke. GETs that succeeded on a retry only add to `retried_count`; the request
   interval the run ended with goes in `final_request_interval`.

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
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

import httpx
import psycopg

from martom.olx import store
from martom.olx.client import OlxApiError, OlxClient, OlxRateLimitAbortError
from martom.olx.detail import (
    DetailFetchAbortedError,
    DetailFetchResult,
    fetch_changed_details,
    needs_detail_fetch,
)
from martom.olx.sync import OlxResponseError, deactivation_candidates, sync_shop

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
    error: str | None = None
    run_id: int | None = field(default=None, compare=False)

    def columns(self) -> dict[str, object]:
        """The values for `store.finish_run` (all but `kind`, `scheduled` and `run_id`)."""
        values = asdict(self)  # dataclass -> dict, like `{...stats}` in JS
        del values["kind"], values["scheduled"], values["run_id"]
        return values


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
) -> RunStats:
    """Run one sync (`nightly` or `full`) and record it in `sync_run`; return its stats.

    `scheduled`: started by the scheduler, so it may auto-apply (module docstring).
    `now` stamps `sync_run.started_at` (default: the database's now()); tests pass a fake.

    OLX failures don't raise: they end the run as 'partial' (OLX stopped it early) or
    'failed' (see the module docstring). Anything else (a database error, a bug) is
    recorded as 'failed' when the database still allows it, then re-raised.
    """
    if kind == "apply_skipped":
        raise ValueError("use apply_skipped_run for kind 'apply_skipped'")
    return await _recorded(
        conn,
        olx,
        kind,
        lambda stats: _sync(conn, olx, stats),
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
    recovered_at_start = olx.recovered_requests

    def take_client_counters() -> None:
        stats.retried_count = olx.recovered_requests - recovered_at_start
        stats.final_request_interval = olx.request_interval

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
        take_client_counters()
        await _finish_quietly(conn, stats)
        raise
    take_client_counters()
    await store.finish_run(conn, stats.run_id, stats.columns())
    _log_summary(stats, olx)
    return stats


async def _sync(conn: psycopg.AsyncConnection, olx: OlxClient, stats: RunStats) -> None:
    """One nightly/full run; when it stops early, carry over the ids still to do.

    That holds for every way it can stop (OLX stopping it -> 'partial', or any other
    error -> 'failed'), so the run after it still takes those ids first.
    """
    assert stats.run_id is not None  # set by `_recorded` before the run starts
    # Listings whose GET still failed last run go first, so a short run retries them.
    failed_last_run = await store.previous_failed_ids(conn, stats.run_id)
    # Ids that still need a GET this run (detail fetch or re-check), kept up to date as
    # the run goes, for the case that it stops early: those go to the next run.
    todo = set(failed_last_run)
    try:
        await _sync_steps(conn, olx, stats, failed_last_run, todo)
    except Exception:
        _carry_over(stats, todo)
        raise


async def _sync_steps(
    conn: psycopg.AsyncConnection,
    olx: OlxClient,
    stats: RunStats,
    failed_last_run: set[int],
    todo: set[int],
) -> None:
    assert stats.run_id is not None
    result = await sync_shop(olx)
    stats.shop_total = result.shop_total
    stats.coverage = result.coverage
    stats.seen = result.unique_count

    stored = await store.load_listings(conn)
    # A full run pretends no hash is stored, so every listing is fetched again.
    stored_hashes = (
        {}
        if stats.kind == "full"
        else {i: s.summary_hash for i, s in stored.items() if s.summary_hash is not None}
    )
    # Summaries first, then details batch by batch, so a crash or a dropped connection
    # an hour into a full run loses one batch, not every detail fetched so far.
    await store.save_summaries(conn, result.listings)
    stats.new = sum(1 for i in result.listings if i not in stored)
    stats.reactivated = sum(1 for i in result.listings if i in stored and not stored[i].active)
    todo.update(
        i for i, row in result.listings.items() if needs_detail_fetch(row, stored_hashes.get(i))
    )
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

    candidates = deactivation_candidates((i for i, s in stored.items() if s.active), result)
    stats.deactivation_candidates = len(candidates)
    if not candidates:
        return
    # All of them until the re-check is done: an abort midway loses its answers so far.
    todo.update(candidates)
    recheck = await recheck_listings(olx, candidates, await shop_id(olx), first=failed_last_run)
    todo.difference_update(candidates)
    _record_failures(stats, recheck.errors)
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


def _log_summary(stats: RunStats, olx: OlxClient) -> None:
    """One line per run. Besides the `sync_run` columns it carries the OLX timing
    (requests, 429s, the wait OLX asked for, elapsed) that #105 needs and that has no
    column. `olx_429_requested_wait_seconds` is the sum of the 429 waits OLX asked for
    (Retry-After, or our backoff), not time actually slept.

    The counters are the client's totals; `martom sync` uses one client per run.
    """
    timing = {
        "olx_requests_sent": olx.requests_sent,
        "olx_429_count": olx.rate_limited_count,
        "olx_429_requested_wait_seconds": olx.rate_limit_requested_wait_seconds,
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
        olx.rate_limited_count,
        olx.rate_limit_requested_wait_seconds,
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
    "recheck_allowed",
    "recheck_listings",
    "run_outcome",
    "run_sync",
    "shop_id",
]
