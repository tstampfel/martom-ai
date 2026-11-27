"""Database side of the review checks: load what the checks read, save what they find.

Reads only the `active_olx_listing` view (CLAUDE.md): a sold listing is never checked, and
its open issues are resolved on the next run. Plain SQL through psycopg 3's async API.
"""

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from martom.extract.store import VariantRow
from martom.review.checks import CheckListing, CheckProduct, ReviewIssue

# The photo count comes from the listing-page row OLX sent (`summary`): its `images` list,
# or the single `image` when the list is missing or empty.
_LISTINGS = """
SELECT l.id, l.slug, l.title, l.description_text, l.price, l.display_price,
       l.price_by_agreement, l.state, l.attributes,
       CASE WHEN jsonb_typeof(l.summary -> 'images') = 'array'
                 AND jsonb_array_length(l.summary -> 'images') > 0
            THEN jsonb_array_length(l.summary -> 'images')
            WHEN coalesce(l.summary ->> 'image', '') <> '' THEN 1
            ELSE 0 END,
       p.listing_id IS NOT NULL, p.product_type, p.material
FROM active_olx_listing AS l
LEFT JOIN product AS p ON p.listing_id = l.id
ORDER BY l.id
"""

_VARIANTS = """
SELECT v.listing_id, v.label, v.height, v.length, v.colour, v.size, v.price_text, v.price,
       v.unit
FROM product_variant AS v
JOIN active_olx_listing AS l ON l.id = v.listing_id
ORDER BY v.listing_id, v.position
"""


def _attributes(value: object) -> tuple[tuple[str, str], ...]:
    """olx_listing.attributes is jsonb: [{"name": ..., "value": ...}, ...]."""
    if not isinstance(value, list):
        return ()
    return tuple(
        (str(a["name"]), str(a["value"]))
        for a in value
        if isinstance(a, dict) and a.get("name") and a.get("value") is not None
    )


async def load_check_listings(conn: psycopg.AsyncConnection) -> list[CheckListing]:
    """Every active listing with its extracted product (if it has one yet)."""
    variants: dict[int, list[VariantRow]] = defaultdict(list)
    cursor = await conn.execute(_VARIANTS)
    for row in await cursor.fetchall():
        variants[row[0]].append(VariantRow(*row[1:]))

    cursor = await conn.execute(_LISTINGS)
    listings = []
    for row in await cursor.fetchall():
        (listing_id, slug, title, description, price, display_price, by_agreement, state,
         attributes, photo_count, has_product, product_type, material) = row  # fmt: skip
        listings.append(
            CheckListing(
                id=listing_id,
                slug=slug,
                title=title or "",
                description_text=description or "",
                price=price,
                display_price=display_price,
                price_by_agreement=bool(by_agreement),
                state=state,
                attributes=_attributes(attributes),
                photo_count=photo_count,
                product=CheckProduct(product_type, material, variants[listing_id])
                if has_product
                else None,
            )
        )
    return listings


# The allowlist warnings of the latest nightly/full sync that ran the guard (#45). NULL
# means the guard didn't run (migration 008), so such a run is skipped: it says nothing.
_LATEST_ALLOWLIST_WARNINGS = """
SELECT allowlist_warnings FROM sync_run
WHERE kind IN ('nightly', 'full') AND allowlist_warnings IS NOT NULL
ORDER BY id DESC
LIMIT 1
"""
# The same, from the latest run that paged every listing (coverage 100%).
_LATEST_COMPLETE_ALLOWLIST_WARNINGS = """
SELECT allowlist_warnings FROM sync_run
WHERE kind IN ('nightly', 'full') AND allowlist_warnings IS NOT NULL AND coverage >= 1
ORDER BY id DESC
LIMIT 1
"""


async def _latest_allowlist_warnings(conn: psycopg.AsyncConnection, query: str) -> list[Any]:
    cursor = await conn.execute(query)  # type: ignore[arg-type]
    row = await cursor.fetchone()
    return list(row[0]) if row is not None and isinstance(row[0], list) else []


def _is_not_seen(warning: object) -> bool:
    return isinstance(warning, dict) and warning.get("reason") == "not_seen"


async def load_allowlist_warnings(conn: psycopg.AsyncConnection) -> list[Any]:
    """The allowlist warnings the review raises issues from; empty before any check.

    Two sources, because the two kinds of evidence differ (R1A-1):

    - "not_seen" (the sync found the listing in no category) is only proven by a run that
      paged every listing, so it comes from the latest run with 100% coverage, the same
      rule as deactivation. A partial run neither raises it nor resolves an open one.
    - Everything else (404, inactive, not_martom from a GET that answered; recheck_failed,
      which raises nothing) comes from the latest run that ran the guard, whatever its
      coverage: a GET doesn't depend on the paging.
    """
    latest = await _latest_allowlist_warnings(conn, _LATEST_ALLOWLIST_WARNINGS)
    complete = await _latest_allowlist_warnings(conn, _LATEST_COMPLETE_ALLOWLIST_WARNINGS)
    return [w for w in latest if not _is_not_seen(w)] + [w for w in complete if _is_not_seen(w)]


# A problem found again: refresh the text and last_seen_at. A resolved issue that is back
# reopens; an ignored one stays ignored (Toni already decided). `xmax = 0` is true only for
# a freshly inserted row, which is how the count of new issues is taken.
_UPSERT = """
INSERT INTO review_issue (fingerprint, kind, listing_id, priority, message, details)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (fingerprint) DO UPDATE SET
    kind = EXCLUDED.kind,
    listing_id = EXCLUDED.listing_id,
    priority = EXCLUDED.priority,
    message = EXCLUDED.message,
    details = EXCLUDED.details,
    last_seen_at = now(),
    status = CASE WHEN review_issue.status = 'resolved' THEN 'open' ELSE review_issue.status END,
    resolved_at = CASE WHEN review_issue.status = 'resolved' THEN NULL
                       ELSE review_issue.resolved_at END
RETURNING (xmax = 0)
"""

# Open issues this run didn't find: fixed on OLX ('not_found'), or their listing is no
# longer active ('listing_inactive'). The reason goes into details.resolved_because, so the
# report (M2-3) can tell the two apart; a reopened issue gets fresh details without it.
_RESOLVE = """
UPDATE review_issue AS r SET
    status = 'resolved',
    resolved_at = now(),
    details = r.details || jsonb_build_object(
        'resolved_because',
        CASE WHEN EXISTS (
                 -- the issue's listing, or for a duplicate any listing of the group
                 SELECT 1
                 FROM (SELECT r.listing_id AS id
                       UNION ALL
                       SELECT jsonb_array_elements_text(
                                  CASE WHEN jsonb_typeof(r.details -> 'listing_ids') = 'array'
                                       THEN r.details -> 'listing_ids' ELSE '[]' END)::bigint
                      ) AS involved
                 WHERE involved.id IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM active_olx_listing AS l WHERE l.id = involved.id))
             THEN 'listing_inactive' ELSE 'not_found' END)
WHERE r.status = 'open' AND NOT (r.fingerprint = ANY(%s)) AND NOT (r.kind = ANY(%s))
"""


def distinct_fingerprints(issues: Sequence[ReviewIssue]) -> dict[str, ReviewIssue]:
    """Each issue under its own fingerprint; none is ever dropped.

    The checks already give every issue a distinct key, so this is a safety net: if two
    issues still share a fingerprint, the later ones get the fingerprint with "#2", "#3"
    appended (hashed again). The checks run in a fixed order (listings by id, variants by
    position), so the same input gives the same fingerprints on every run.
    """
    result: dict[str, ReviewIssue] = {}
    for issue in issues:
        fingerprint, n = issue.fingerprint, 1
        while fingerprint in result:
            n += 1
            fingerprint = hashlib.sha256(f"{issue.fingerprint}#{n}".encode()).hexdigest()
        result[fingerprint] = issue
    return result


async def save_issues(
    conn: psycopg.AsyncConnection,
    issues: Sequence[ReviewIssue],
    *,
    unchecked_kinds: Sequence[str] = (),
) -> tuple[int, int, int]:
    """Upsert every issue and resolve the open ones not in `issues`, in one transaction.

    `issues` must be the result of a run over the whole catalogue: anything open that isn't
    in it is marked resolved, except issues of `unchecked_kinds` (a check that couldn't
    run this time, e.g. a broken price map), which stay as they are. Returns (rows
    written, new issues, resolved issues).
    """
    unique = distinct_fingerprints(issues)
    new = 0
    async with conn.transaction(), conn.cursor() as cursor:
        for fingerprint, issue in unique.items():
            await cursor.execute(
                _UPSERT,
                (
                    fingerprint,
                    issue.kind,
                    issue.listing_id,
                    issue.priority,
                    issue.message,
                    Jsonb(issue.details),
                ),
            )
            row = await cursor.fetchone()
            new += bool(row and row[0])
        await cursor.execute(_RESOLVE, (list(unique), list(unchecked_kinds)))
        resolved = cursor.rowcount
    return len(unique), new, resolved
