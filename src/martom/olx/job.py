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
   'partial' below 100% coverage, 'skipped_guard' when a guard skipped deactivation
   (`run_outcome`), 'failed' when the run broke.

Getting a real large removal past the 10% guard:

- **Automatic:** when a run trips the guard and the run before it (nightly or full)
  tripped it too, this second run deactivates the ids flagged in both runs that its own
  re-check confirms, without the 10% guard (Tomislav, 26 Nov). If the
  re-check fails for more than 20% of those ids, it applies none of them and flags the
  run (`recheck_skipped`, status 'skipped_guard'). The other confirmed ids stay skipped
  and are stored in `skipped_listing_ids`, so the next run can match them.
- **Manual:** `martom sync --apply-skipped <run id>` (`apply_skipped_run`) re-checks the
  ids stored for that skipped run and deactivates the confirmed ones, with the same
  per-listing rules and the same 20% re-check guard. It is its own `sync_run`
  (kind `apply_skipped`).

`OlxRateLimitAbortError` (OLX asked for a pause over the 1-hour ceiling) ends the run at
once: what was fetched before it is saved, nothing is deactivated, the run is recorded as
'failed' and the CLI exits 1. Any other OLX failure that stops a step fails the run the
same way; the next scheduled run tries again.
"""

import itertools
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

import httpx
import psycopg

from martom.olx import store
from martom.olx.client import OlxApiError, OlxClient, OlxRateLimitAbortError
from martom.olx.detail import DetailFetchAbortedError, DetailFetchResult, fetch_changed_details
from martom.olx.sync import OlxResponseError, deactivation_candidates, sync_shop

logger = logging.getLogger(__name__)

RunKind = Literal["nightly", "full", "apply_skipped"]
RunStatus = Literal["succeeded", "partial", "skipped_guard", "failed"]
GoneReason = Literal["404", "inactive", "not_martom"]

# At most this share of the active listings may be deactivated in one run (10% guard).
MAX_DEACTIVATION_PERCENT = 10
# Applying skipped ids is called off when the re-check fails for more than this share.
MAX_RECHECK_FAILURE_PERCENT = 20
# Fetched details are saved every this many listings (about 4 minutes at 1.3 s each).
DETAIL_BATCH_SIZE = 200


@dataclass
class RunStats:
    """What one run did; the fields match the `sync_run` columns of the same name."""

    kind: RunKind
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
    errors: int = 0
    deactivation_skipped: bool = False
    would_deactivate: int | None = None
    skipped_listing_ids: list[int] | None = None
    applied_run_ids: list[int] | None = None
    recheck_skipped: bool = False
    error: str | None = None
    run_id: int | None = field(default=None, compare=False)

    def columns(self) -> dict[str, object]:
        """The values for `store.finish_run` (everything but `kind` and `run_id`)."""
        values = asdict(self)  # dataclass -> dict, like `{...stats}` in JS
        del values["kind"], values["run_id"]
        return values


def deactivation_allowed(would_deactivate: int, active_count: int) -> bool:
    """True when `would_deactivate` is at most 10% of `active_count` (exactly 10% is fine).

    Integer math, so 1 of 10 is not lost to float rounding.
    """
    return would_deactivate * 100 <= active_count * MAX_DEACTIVATION_PERCENT


def run_outcome(stats: "RunStats") -> RunStatus:
    """The status of a run that didn't break (see the `sync_run.status` comment in 002).

    Precedence: skipped_guard > partial > succeeded ('failed' is set by the caller when
    the run breaks). `coverage` is None for `--apply-skipped`, which pages nothing.
    """
    if stats.deactivation_skipped or stats.recheck_skipped:
        return "skipped_guard"
    if stats.coverage is not None and stats.coverage < 1:
        return "partial"
    return "succeeded"


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


async def recheck_listings(olx: OlxClient, listing_ids: Collection[int], owner_id: int) -> Recheck:
    """GET /listings/{id} for each id (through the rate-limited client) and sort the answers.

    Gone: 404, a `status` other than "active" ("inactive"), or a `user.id` other than
    `owner_id` ("not_martom"). Still active: status "active" and Martom's. Everything
    else is an error and never counts as removed. Each id is logged with its outcome.
    `OlxRateLimitAbortError` propagates: the caller ends the run and deactivates nothing.
    """
    result = Recheck()
    for listing_id in sorted(listing_ids):
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


async def run_sync(conn: psycopg.AsyncConnection, olx: OlxClient, *, kind: RunKind) -> RunStats:
    """Run one sync (`nightly` or `full`) and record it in `sync_run`; return its stats.

    OLX failures don't raise: they end the run with status 'failed' (see the module
    docstring). Anything else (a database error, a bug) is recorded as 'failed' when the
    database still allows it, then re-raised.
    """
    if kind == "apply_skipped":
        raise ValueError("use apply_skipped_run for kind 'apply_skipped'")
    return await _recorded(conn, kind, lambda stats: _sync(conn, olx, stats))


async def apply_skipped_run(
    conn: psycopg.AsyncConnection, olx: OlxClient, skipped_run_id: int
) -> RunStats:
    """`martom sync --apply-skipped <run id>`: apply the ids a guard-skipped run stored.

    Only those ids, never a fresh list. Each is re-checked with GET /listings/{id}; only
    the confirmed ones are deactivated. More than 20% re-check failures apply nothing and
    end the run as 'skipped_guard', so it can be tried again later.
    """

    async def apply(stats: RunStats) -> None:
        stats.applied_run_ids = [skipped_run_id]
        ids = await store.load_skipped_ids(conn, skipped_run_id)
        if not ids:
            raise _RunFailedError(f"sync run {skipped_run_id} has no skipped deactivations")
        stats.deactivation_candidates = len(ids)
        recheck = await recheck_listings(olx, ids, await shop_id(olx))
        stats.errors = len(recheck.errors)
        if not recheck_allowed(len(recheck.errors), len(ids)):
            _flag_recheck_failure(stats, recheck, ids)  # status becomes skipped_guard
            return
        await _deactivate(conn, recheck.gone, stats, trigger=f"applying run {skipped_run_id}")

    return await _recorded(conn, "apply_skipped", apply)


class _RunFailedError(Exception):
    """Ends a run as 'failed' with this message (not an OLX error)."""


async def _recorded(
    conn: psycopg.AsyncConnection,
    kind: RunKind,
    body: Callable[[RunStats], Awaitable[None]],
) -> RunStats:
    """Open a `sync_run` row, run `body`, and record how it ended."""
    stats = RunStats(kind=kind)
    stats.run_id = await store.start_run(conn, kind)
    logger.info("sync run %d (%s) started", stats.run_id, kind)
    try:
        await body(stats)
        stats.status = run_outcome(stats)
    except (OlxApiError, _RunFailedError) as exc:  # OLX errors include the rate-limit abort
        stats.status = "failed"
        stats.error = str(exc)
        logger.error("sync run %d (%s) failed: %s", stats.run_id, kind, exc)
    except Exception as exc:
        stats.status = "failed"
        stats.error = f"{type(exc).__name__}: {exc}"
        await _finish_quietly(conn, stats)
        raise
    await store.finish_run(conn, stats.run_id, stats.columns())
    _log_summary(stats)
    return stats


async def _sync(conn: psycopg.AsyncConnection, olx: OlxClient, stats: RunStats) -> None:
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
    for batch in itertools.batched(result.listings, DETAIL_BATCH_SIZE):
        rows = {i: result.listings[i] for i in batch}
        try:
            details = await fetch_changed_details(olx, rows, stored_hashes)
        except DetailFetchAbortedError as exc:
            await _save_details(conn, exc.partial, stored, stats)
            raise
        await _save_details(conn, details, stored, stats)

    candidates = deactivation_candidates((i for i, s in stored.items() if s.active), result)
    stats.deactivation_candidates = len(candidates)
    if not candidates:
        return
    recheck = await recheck_listings(olx, candidates, await shop_id(olx))
    stats.errors += len(recheck.errors)
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
    assert stats.run_id is not None  # set by `_recorded` before the run starts
    previous = await store.previous_skipped_run(conn, stats.run_id)
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
) -> None:
    """Save one batch of fetched details and add it to the fetched / changed / error counts."""
    await store.save_details(conn, details.fetched)
    stats.fetched += len(details.fetched)
    stats.errors += len(details.failed)
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


def _log_summary(stats: RunStats) -> None:
    log = logger.info if stats.status == "succeeded" else logger.error
    log(
        "sync run %s (%s) %s: seen %d, new %d, deactivated %d, reactivated %d, "
        "fetched %d, changed %d, errors %d, coverage %s",
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
        extra={"event": "sync_run", **stats.columns(), "run_id": stats.run_id, "kind": stats.kind},
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
