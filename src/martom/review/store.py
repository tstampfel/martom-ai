"""Database side of the review checks: load what the checks read, save what they find.

Reads only the `active_olx_listing` view (CLAUDE.md): a sold listing is never checked, and
its open issues are resolved on the next run. Plain SQL through psycopg 3's async API.
"""

from collections import defaultdict
from collections.abc import Sequence

import psycopg
from psycopg.types.json import Jsonb

from martom.extract.store import VariantRow
from martom.review.checks import CheckListing, CheckProduct, ReviewIssue

# The photo count comes from the listing-page row OLX sent (`summary`): its `images` list,
# or the single `image` when the list is missing.
_LISTINGS = """
SELECT l.id, l.slug, l.title, l.description_text, l.price, l.display_price,
       l.price_by_agreement, l.state, l.attributes,
       CASE WHEN jsonb_typeof(l.summary -> 'images') = 'array'
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

# Open issues this run didn't find are fixed (or their listing is gone).
_RESOLVE = """
UPDATE review_issue SET status = 'resolved', resolved_at = now()
WHERE status = 'open' AND NOT (fingerprint = ANY(%s))
"""


async def save_issues(
    conn: psycopg.AsyncConnection, issues: Sequence[ReviewIssue]
) -> tuple[int, int]:
    """Upsert every issue and resolve the open ones not in `issues`, in one transaction.

    `issues` must be the result of a run over the whole catalogue: anything open that isn't
    in it is marked resolved. Returns (new issues, resolved issues).
    """
    # Two checks could, in theory, produce the same fingerprint; keep the last one.
    unique = {issue.fingerprint: issue for issue in issues}
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
        await cursor.execute(_RESOLVE, (list(unique),))
        resolved = cursor.rowcount
    return new, resolved
