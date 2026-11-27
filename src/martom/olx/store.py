"""Database side of the sync (M1-4): read and write `olx_listing` and `sync_run`.

Plain SQL through psycopg 3's async API. Every function takes an open
`psycopg.AsyncConnection` in autocommit mode; functions that write several rows wrap them
in `conn.transaction()`, so a crash halfway leaves the table as it was (like a
`BEGIN ... COMMIT` block that rolls back on an exception).

The job logic (what to write, when to deactivate) lives in `olx.job`; this module only
knows the tables.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from martom.olx.detail import FetchedDetail


@dataclass(frozen=True)
class StoredListing:
    """What the job needs to know about a listing already in the database."""

    summary_hash: str | None  # None: its detail was never fetched
    detail_hash: str | None
    active: bool


async def load_listings(conn: psycopg.AsyncConnection) -> dict[int, StoredListing]:
    """Every listing in `olx_listing`, active or not: id -> its hashes and active flag."""
    cursor = await conn.execute("SELECT id, summary_hash, detail_hash, active FROM olx_listing")
    return {
        row[0]: StoredListing(summary_hash=row[1], detail_hash=row[2], active=row[3])
        for row in await cursor.fetchall()
    }


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


_UPSERT_SUMMARY = """
INSERT INTO olx_listing (id, slug, title, category_id, top_category_id, summary)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    slug = EXCLUDED.slug,
    title = EXCLUDED.title,
    category_id = EXCLUDED.category_id,
    top_category_id = EXCLUDED.top_category_id,
    summary = EXCLUDED.summary,
    last_seen_at = now(),
    active = true,          -- seen on the listing pages, so it is for sale again
    deactivated_at = NULL
"""

# The summary hash is written only here, together with the detail it belongs to.
_UPDATE_DETAIL = """
UPDATE olx_listing SET
    slug = %(slug)s,
    title = %(title)s,
    short_description = %(short_description)s,
    description_html = %(description_html)s,
    description_text = %(description_text)s,
    price = %(price)s,
    display_price = %(display_price)s,
    price_by_agreement = %(price_by_agreement)s,
    state = %(state)s,
    status = %(status)s,
    available = %(available)s,
    brand = %(brand)s,
    attributes = %(attributes)s,
    summary_hash = %(summary_hash)s,
    detail_hash = %(detail_hash)s,
    detail_fetched_at = now()
WHERE id = %(id)s
"""


async def save_summaries(
    conn: psycopg.AsyncConnection, rows: Mapping[int, Mapping[str, Any]]
) -> None:
    """Upsert every seen listing-page row in one transaction.

    `rows` is listing id -> listing-page row (every listing seen this run). It doesn't
    touch `summary_hash`, so a listing whose detail isn't saved yet (NULL for a new one)
    is fetched again by the next run. A seen listing is (re)activated: it is on the
    shop's listing pages right now.
    """
    if not rows:
        return
    async with conn.transaction(), conn.cursor() as cursor:
        await cursor.executemany(
            _UPSERT_SUMMARY,
            [
                (
                    listing_id,
                    _str_or_none(row.get("slug")),
                    _str_or_none(row.get("title")),
                    _int_or_none(row.get("category_id")),
                    _int_or_none(row.get("top_category_id")),
                    Jsonb(dict(row)),
                )
                for listing_id, row in rows.items()
            ],
        )


async def save_details(conn: psycopg.AsyncConnection, details: Mapping[int, FetchedDetail]) -> None:
    """Store fetched details with their summary hashes, in one transaction.

    Only listings whose detail GET succeeded are here, so a failed fetch keeps its old
    summary hash and is retried next run. The rows must exist (`save_summaries` first).
    """
    if not details:
        return
    async with conn.transaction(), conn.cursor() as cursor:
        await cursor.executemany(
            _UPDATE_DETAIL,
            [
                {
                    "id": listing_id,
                    "slug": fetched.detail.slug,
                    "title": fetched.detail.title,
                    "short_description": fetched.detail.short_description,
                    "description_html": fetched.detail.description_html,
                    "description_text": fetched.detail.description_text,
                    "price": fetched.detail.price,
                    "display_price": fetched.detail.display_price,
                    "price_by_agreement": fetched.detail.price_by_agreement,
                    "state": fetched.detail.state,
                    "status": fetched.detail.status,
                    "available": fetched.detail.available,
                    "brand": fetched.detail.brand,
                    "attributes": Jsonb(
                        [
                            {"name": name, "value": value}
                            for name, value in fetched.detail.attributes
                        ]
                    ),
                    "summary_hash": fetched.summary_hash,
                    "detail_hash": fetched.detail.detail_hash,
                }
                for listing_id, fetched in details.items()
            ],
        )


async def count_active(conn: psycopg.AsyncConnection) -> int:
    cursor = await conn.execute("SELECT count(*) FROM olx_listing WHERE active")
    row = await cursor.fetchone()
    assert row is not None  # count(*) always returns one row
    return int(row[0])


async def deactivate(conn: psycopg.AsyncConnection, listing_ids: Collection[int]) -> list[int]:
    """Mark these listings inactive (never deleted); return the ids that actually changed.

    Ids already inactive (or unknown) are left alone and not returned.
    """
    if not listing_ids:
        return []
    cursor = await conn.execute(
        "UPDATE olx_listing SET active = false, deactivated_at = now() "
        "WHERE id = ANY(%s) AND active RETURNING id",
        (sorted(listing_ids),),
    )
    return sorted(row[0] for row in await cursor.fetchall())


async def start_run(
    conn: psycopg.AsyncConnection,
    kind: str,
    *,
    scheduled: bool = False,
    started_at: datetime | None = None,
) -> int:
    """Insert a `sync_run` row with status 'running' and return its id.

    Committed at once (autocommit), so a run that crashes hard still leaves a visible
    'running' row with no `finished_at` for the freshness alert to find. `started_at`
    defaults to the database's now(); tests pass a fake clock's time.
    """
    cursor = await conn.execute(
        "INSERT INTO sync_run (kind, scheduled, started_at) "
        "VALUES (%s, %s, coalesce(%s, now())) RETURNING id",
        (kind, scheduled, started_at),
    )
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


# The columns `finish_run` may set; anything else in `values` is a programming error.
RUN_COLUMNS = frozenset(
    {
        "status", "shop_total", "coverage", "seen", "new", "fetched", "changed",
        "deactivation_candidates", "deactivated", "reactivated", "errors",
        "deactivation_skipped", "would_deactivate", "skipped_listing_ids",
        "applied_run_ids", "recheck_skipped", "retried_count", "failed_listing_ids",
        "final_request_interval", "error", "extracted",
    }
)  # fmt: skip


async def finish_run(conn: psycopg.AsyncConnection, run_id: int, values: Mapping[str, Any]) -> None:
    """Write the run's final stats and status, and stamp `finished_at`."""
    unknown = set(values) - RUN_COLUMNS
    if unknown:
        raise ValueError(f"unknown sync_run columns: {sorted(unknown)}")
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder(name)) for name in values
    )
    query = sql.SQL("UPDATE sync_run SET {}, finished_at = now() WHERE id = {}").format(
        assignments, sql.Placeholder("_run_id")
    )
    await conn.execute(query, {**values, "_run_id": run_id})


async def load_skipped_ids(conn: psycopg.AsyncConnection, run_id: int) -> list[int]:
    """The listing ids a guard-skipped run stored; empty if it skipped none (or is unknown)."""
    cursor = await conn.execute(
        "SELECT skipped_listing_ids FROM sync_run WHERE id = %s AND deactivation_skipped",
        (run_id,),
    )
    row = await cursor.fetchone()
    return list(row[0] or []) if row is not None else []


# The scheduled nightly/full run right before run %(run_id)s, and whether it started at
# least MIN_HOURS_BETWEEN_TRIPS before that run.
_PREVIOUS_SCHEDULED_RUN = """
SELECT prev.id, prev.deactivation_skipped, prev.skipped_listing_ids,
       prev.started_at <= this.started_at - make_interval(hours => %(min_hours)s)
FROM sync_run AS this
JOIN sync_run AS prev
  ON prev.id < this.id AND prev.kind IN ('nightly', 'full') AND prev.scheduled
WHERE this.id = %(run_id)s
ORDER BY prev.id DESC
LIMIT 1
"""

# The two guard trips of an automatic apply must start at least this far apart.
MIN_HOURS_BETWEEN_TRIPS = 12


async def previous_skipped_run(
    conn: psycopg.AsyncConnection, before_run_id: int
) -> tuple[int, set[int]] | None:
    """The scheduled sync run right before `before_run_id`, if it tripped the 10% guard
    and started at least 12 h earlier: its id and the listing ids it left skipped.

    Only scheduled `nightly` and `full` runs count (R1-8): a manual run (by hand, or
    `apply_skipped`) neither makes nor breaks a streak. Any other scheduled run in
    between (succeeded, partial, failed or crashed) breaks it, so the two scheduled trips
    must be consecutive. If the previous scheduled run started less than 12 h earlier
    (say, the weekly full run two hours after the nightly), there is no auto-apply.
    """
    cursor = await conn.execute(
        _PREVIOUS_SCHEDULED_RUN,
        {"run_id": before_run_id, "min_hours": MIN_HOURS_BETWEEN_TRIPS},
    )
    row = await cursor.fetchone()
    if row is None or not row[1] or not row[2] or not row[3]:
        return None
    return int(row[0]), set(row[2])


async def previous_failed_ids(conn: psycopg.AsyncConnection, before_run_id: int) -> set[int]:
    """The listing ids whose GET still failed in the last nightly/full run before this one."""
    cursor = await conn.execute(
        "SELECT failed_listing_ids FROM sync_run "
        "WHERE kind IN ('nightly', 'full') AND id < %s ORDER BY id DESC LIMIT 1",
        (before_run_id,),
    )
    row = await cursor.fetchone()
    return set(row[0] or []) if row is not None else set()
