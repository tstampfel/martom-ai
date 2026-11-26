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


async def save_listings(
    conn: psycopg.AsyncConnection,
    rows: Mapping[int, Mapping[str, Any]],
    details: Mapping[int, FetchedDetail],
) -> None:
    """Upsert every seen listing-page row, then store the fetched details, in one transaction.

    `rows` is listing id -> listing-page row (every listing seen this run); `details`
    holds only the listings whose detail GET succeeded. A seen listing without a detail
    keeps its old `summary_hash` (NULL for a new one), so the next run fetches it again.
    A seen listing is (re)activated: it is on the shop's listing pages right now.
    """
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


async def deactivate(conn: psycopg.AsyncConnection, listing_ids: Collection[int]) -> int:
    """Mark these listings inactive (never deleted); return how many changed."""
    if not listing_ids:
        return 0
    cursor = await conn.execute(
        "UPDATE olx_listing SET active = false, deactivated_at = now() "
        "WHERE id = ANY(%s) AND active",
        (sorted(listing_ids),),
    )
    return cursor.rowcount


async def start_run(conn: psycopg.AsyncConnection, kind: str) -> int:
    """Insert a `sync_run` row with status 'running' and return its id.

    Committed at once (autocommit), so a run that crashes hard still leaves a visible
    'running' row with no `finished_at` for the freshness alert to find.
    """
    cursor = await conn.execute("INSERT INTO sync_run (kind) VALUES (%s) RETURNING id", (kind,))
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


# The columns `finish_run` may set; anything else in `values` is a programming error.
RUN_COLUMNS = frozenset(
    {
        "status", "shop_total", "coverage", "seen", "new", "fetched", "changed",
        "deactivation_candidates", "deactivated", "reactivated", "errors",
        "deactivation_skipped", "would_deactivate", "skipped_listing_ids",
        "applied_run_ids", "recheck_skipped", "error",
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


async def previous_skipped_run(
    conn: psycopg.AsyncConnection, before_run_id: int
) -> tuple[int, set[int]] | None:
    """The sync run right before `before_run_id`, if it tripped the 10% guard: its id and
    the listing ids it left skipped. None when that run didn't trip the guard.

    Only `nightly` and `full` runs count: a manual `apply_skipped` run doesn't break or
    make a streak. Any other sync run in between (succeeded, partial, failed or crashed)
    does, so the two trips must be consecutive.
    """
    cursor = await conn.execute(
        "SELECT id, deactivation_skipped, skipped_listing_ids FROM sync_run "
        "WHERE kind IN ('nightly', 'full') AND id < %s ORDER BY id DESC LIMIT 1",
        (before_run_id,),
    )
    row = await cursor.fetchone()
    if row is None or not row[1] or not row[2]:
        return None
    return int(row[0]), set(row[2])
