"""`martom extract`: extract every active listing whose detail changed, record a sync_run.

1. Start a `sync_run` row (kind 'extraction').
2. Load the active listings with no product row or a product made from an older detail
   hash (`store.load_pending`); `--all` takes every active listing instead.
3. Extract them in batches (4 Claude calls at a time) and save each batch as it finishes,
   so a crash halfway keeps what was already done.
4. Run the review checks over the whole catalogue (`martom.review.job`), which upsert
   Toni's `review_issue` rows. Skipped when the run failed.
5. Finish the run: 'succeeded', 'partial' (some listings still failed after their
   retries; they keep their old product row and are tried again next run, because their
   hashes still differ; or the review checks broke, so the queue is out of date) or
   'failed' (the run broke: a database error, a non-retryable API error such as a bad
   key, 20 listings in a row failing, or nothing extracted).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import psycopg

from martom.extract.extractor import ExtractionResult, Extractor
from martom.extract.prices import parse_price_km
from martom.extract.prompt import PROMPT_VERSION, ListingInput
from martom.extract.schema import ExtractedProduct
from martom.extract.search_text import build_search_text
from martom.extract.store import ProductRecord, VariantRow, load_pending, save_products
from martom.olx.store import finish_run, start_run
from martom.review.job import ReviewStats, run_review_checks

logger = logging.getLogger(__name__)

# Listings per batch: extracted concurrently, then saved in one transaction.
BATCH_SIZE = 40
# This many failures in a row (checked after each batch) look like an outage, not bad
# listings: the run stops as 'failed' instead of working through the whole catalogue.
MAX_CONSECUTIVE_FAILURES = 20


class _OutageError(Exception):
    """The run is stopped as 'failed': nothing worked, so something is broken."""


type ExtractionStatus = Literal["succeeded", "partial", "failed"]


@dataclass
class ExtractionStats:
    run_id: int
    status: ExtractionStatus = "succeeded"
    seen: int = 0  # listings that needed extraction this run
    extracted: int = 0
    errors: int = 0
    failed_listing_ids: list[int] = field(default_factory=list)
    error: str | None = None
    review: ReviewStats | None = None  # None when the checks didn't run or broke


def build_record(listing: ListingInput, product: ExtractedProduct, *, model: str) -> ProductRecord:
    """The rows to save for one extracted listing (plain Python after the model)."""
    return ProductRecord(
        listing_id=listing.id,
        detail_hash=listing.detail_hash,
        product_type=product.product_type,
        brand=product.brand,
        material=product.material,
        wire_thickness=product.wire_thickness,
        mesh=product.mesh,
        colours=product.colours,
        uses=product.uses,
        compatible_with=product.compatible_with,
        synonyms=product.synonyms,
        extraction=product.model_dump(mode="json"),
        model=model,
        prompt_version=PROMPT_VERSION,
        search_text=build_search_text(listing.title, product),
        variants=[
            VariantRow(
                label=v.label,
                height=v.height,
                length=v.length,
                colour=v.colour,
                size=v.size,
                price_text=v.price,
                price=parse_price_km(v.price),
                unit=v.unit,
            )
            for v in product.variants
        ],
    )


def _batches(items: Sequence[ListingInput], size: int) -> list[Sequence[ListingInput]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


async def _save_batch(
    conn: psycopg.AsyncConnection,
    listings: Sequence[ListingInput],
    results: Sequence[ExtractionResult],
    stats: ExtractionStats,
    model: str,
) -> None:
    by_id = {listing.id: listing for listing in listings}
    records = []
    for result in results:
        if result.product is None:
            stats.errors += 1
            stats.failed_listing_ids.append(result.listing_id)
        else:
            records.append(build_record(by_id[result.listing_id], result.product, model=model))
    await save_products(conn, records)
    stats.extracted += len(records)


async def run_extraction(
    conn: psycopg.AsyncConnection,
    extractor: Extractor,
    *,
    force_all: bool = False,
    limit: int | None = None,
) -> ExtractionStats:
    """Run one extraction pass and record it in `sync_run`. Never raises for a run error:
    the returned (and recorded) status says 'failed' instead."""
    stats = ExtractionStats(run_id=await start_run(conn, "extraction"))
    try:
        pending = await load_pending(conn, force_all=force_all, limit=limit)
        stats.seen = len(pending)
        logger.info("extraction run started", extra={"run_id": stats.run_id, "pending": stats.seen})
        consecutive_failures = 0
        for batch in _batches(pending, BATCH_SIZE):
            # Raises ExtractionAbortedError on a bad key, wrong model id, rejected request.
            results = await extractor.extract_many(batch)
            await _save_batch(conn, batch, results, stats, extractor.model_name)
            for result in results:
                consecutive_failures = 0 if result.product else consecutive_failures + 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise _OutageError(
                    f"{consecutive_failures} consecutive listings failed; stopped the run "
                    "(is the model or the API down?)"
                )
        if stats.errors and not stats.extracted:
            raise _OutageError(f"no listing was extracted ({stats.errors} failed)")
        stats.status = "partial" if stats.errors else "succeeded"
    except Exception as exc:
        logger.exception("extraction run %s failed", stats.run_id)
        stats.status = "failed"
        stats.error = f"{type(exc).__name__}: {exc}"
    if stats.status != "failed":
        try:
            stats.review = await run_review_checks(conn)
        except Exception as exc:
            # The products are saved; only Toni's queue is stale, so 'partial', not 'failed'.
            logger.exception("review checks after extraction run %s failed", stats.run_id)
            stats.status = "partial"
            stats.error = f"review checks failed: {type(exc).__name__}: {exc}"
    await finish_run(
        conn,
        stats.run_id,
        {
            "status": stats.status,
            "seen": stats.seen,
            "extracted": stats.extracted,
            "errors": stats.errors,
            "failed_listing_ids": stats.failed_listing_ids or None,
            "error": stats.error,
        },
    )
    logger.info(
        "extraction run finished",
        extra={
            "run_id": stats.run_id,
            "status": stats.status,
            "seen": stats.seen,
            "extracted": stats.extracted,
            "errors": stats.errors,
        },
    )
    return stats
