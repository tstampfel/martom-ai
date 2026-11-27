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
from martom.review.checks import run_all_checks
from martom.review.store import load_check_listings, save_issues

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewStats:
    checked: int  # active listings checked
    raised: int  # issues found this run (new or seen before)
    new: int  # of those, first seen this run
    resolved: int  # open issues this run no longer found


async def run_review_checks(
    conn: psycopg.AsyncConnection,
    *,
    today: date | None = None,
    manual_products_path: Path = DEFAULT_PATH,
) -> ReviewStats:
    """Check every active listing and manual_products.toml; upsert by fingerprint."""
    manual_products = load_manual_products(manual_products_path)
    listings = await load_check_listings(conn)
    issues = run_all_checks(listings, manual_products, today=today or date.today())
    new, resolved = await save_issues(conn, issues)
    stats = ReviewStats(checked=len(listings), raised=len(issues), new=new, resolved=resolved)
    logger.info(
        "review checks finished",
        extra={
            "checked": stats.checked,
            "raised": stats.raised,
            "new": stats.new,
            "resolved": stats.resolved,
        },
    )
    return stats
