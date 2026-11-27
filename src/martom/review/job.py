"""Run every review check over the catalogue and save the findings in `review_issue`.

Called at the end of `martom extract` (`martom.extract.job.run_extraction`), so the queue
reflects the latest extraction. The checks are cheap plain Python, so each run checks every
active listing, not only the ones just extracted: that is what lets it resolve issues that
were fixed on OLX and raise the 90-day manual_products reminder on the right day.
"""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from martom.bom.manual_products import DEFAULT_PATH, load_manual_products
from martom.bom.pricing import DEFAULT_PATH as PRICE_MAP_PATH
from martom.bom.pricing import load_price_map
from martom.review.checks import run_all_checks
from martom.review.store import load_allowlist_warnings, load_check_listings, save_issues

logger = logging.getLogger(__name__)


class PriceMapCheckSkippedError(Exception):
    """price_map.toml couldn't be loaded: every other check ran and was saved, the BOM
    price check didn't, and its open issues were left as they were. Raised after saving, so
    `martom extract` marks its run 'partial' (a check that breaks is alert-worthy)."""


@dataclass(frozen=True)
class ReviewStats:
    checked: int  # active listings checked
    raised: int  # problems found this run (new or seen before), one per fingerprint
    new: int  # of those, first seen this run
    resolved: int  # open issues this run no longer found


async def run_review_checks(
    conn: psycopg.AsyncConnection,
    *,
    today: date | None = None,
    manual_products_path: Path = DEFAULT_PATH,
    price_map_path: Path = PRICE_MAP_PATH,
) -> ReviewStats:
    """Check every active listing, manual_products.toml, the latest sync's allowlist
    warnings and the BOM price map (#125); upsert by fingerprint."""
    manual_products = load_manual_products(manual_products_path)
    map_error: Exception | None = None
    try:
        price_map = load_price_map(price_map_path)
    except (OSError, ValueError) as exc:
        # A broken map must not stop the listing, manual and allowlist checks; the shipped
        # map is checked by the tests, and the pricer refuses to start on it.
        logger.exception("price map invalid, BOM price check skipped")
        price_map, map_error = None, exc
    listings = await load_check_listings(conn)
    issues = run_all_checks(
        listings,
        manual_products,
        today=today or date.today(),
        allowlist_warnings=await load_allowlist_warnings(conn),
        price_map=price_map,
    )
    # Unchecked kinds keep their open issues: a skipped check found nothing because it
    # never ran, not because the problems were fixed.
    unchecked = ["bom_price_missing"] if price_map is None else []
    raised, new, resolved = await save_issues(conn, issues, unchecked_kinds=unchecked)
    stats = ReviewStats(checked=len(listings), raised=raised, new=new, resolved=resolved)
    logger.info(
        "review checks finished",
        extra={
            "checked": stats.checked,
            "raised": stats.raised,
            "new": stats.new,
            "resolved": stats.resolved,
        },
    )
    if map_error is not None:
        raise PriceMapCheckSkippedError(
            f"price map invalid, BOM price check skipped: {map_error}"
        ) from map_error
    return stats
