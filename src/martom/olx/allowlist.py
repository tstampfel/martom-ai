"""The allowlist guard (#45): fence listings outside "Ograde" must stay synced.

`allowlist.toml` (next to this file) lists the listings and the category each was
allowlisted in. This module holds the plain parts: loading the file and comparing it to
the rows the category sync saw (`check_seen`). The sync job (`martom.olx.job`) asks OLX
about the ids the sync didn't see and records every warning on the `sync_run` row.

"Synced" means the category sync returned the listing this run: it is among the rows of
`SyncResult.listings` (Martom's own, deduped by id), whatever category OLX put it in. The
sync pages every category the shop has plus a whole-shop sweep, so a listing that moved
from one category to another is still synced; only a listing the sync didn't return at
all (and that OLX says is gone, or still active) is a problem (Tomislav, 27 Nov 2026:
"Only a move OUT of all synced categories should warn").
"""

import logging
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).with_name("allowlist.toml")

# 404 / inactive / not_martom: not synced, and GET /listings/{id} says it is gone
# not_seen:       not synced, though GET /listings/{id} says it is active and Martom's:
#                 it moved out of every category the sync pages (or the sync missed it)
# recheck_failed: not checked: GET /listings/{id} failed (network, timeout, 5xx, ...), or
#                 the synced row had no category_id (error "no_category_id")
# (Runs before R1-3 could also record "moved"; the review check ignores it.)
type AllowlistReason = Literal["404", "inactive", "not_martom", "not_seen", "recheck_failed"]


@dataclass(frozen=True)
class AllowlistedListing:
    id: int
    category_id: int  # the OLX category it was allowlisted in
    name: str  # our name for it (messages), never listing text


@dataclass(frozen=True)
class Allowlist:
    listings: tuple[AllowlistedListing, ...]
    # Category id -> name ("Ograde"): the fence categories. Finding an allowlisted listing
    # in one makes its entry redundant (an INFO line); they don't decide any warning.
    fence_categories: Mapping[int, str]


@dataclass(frozen=True)
class AllowlistWarning:
    """One problem with one allowlisted listing; stored as JSON in the `sync_run` row."""

    listing_id: int
    name: str
    reason: AllowlistReason
    expected_category_id: int
    category_id: int | None = None  # always None now: a listing the sync saw never warns
    error: str | None = None  # recheck_failed: "timeout", "5xx", ...

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _int(value: object, what: str) -> int:
    # bool is a subclass of int in Python, so `true` would pass a plain isinstance check.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"allowlist: {what} must be a whole number")
    return value


def load_allowlist(path: Path = DEFAULT_PATH) -> Allowlist:
    """Read and validate `allowlist.toml`; a bad file raises ValueError."""
    with path.open("rb") as file:  # tomllib wants bytes
        data = tomllib.load(file)

    fence = data.get("fence_categories")
    if not isinstance(fence, dict) or not fence:
        raise ValueError("allowlist: [fence_categories] must list at least one category")
    fence_categories: dict[int, str] = {}
    for key, name in fence.items():
        # TOML keys are always strings ("881"), so turn them back into ids.
        if not key.isdigit() or not isinstance(name, str) or not name.strip():
            raise ValueError('allowlist: [fence_categories] entries are <id> = "<name>"')
        fence_categories[int(key)] = name

    listings: list[AllowlistedListing] = []
    for entry in data.get("listing", []):
        listing_id = _int(entry.get("id"), "id")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"allowlist: listing {listing_id} needs a name")
        category_id = _int(entry.get("category_id"), f"category_id of {listing_id}")
        if any(existing.id == listing_id for existing in listings):
            raise ValueError(f"allowlist: duplicate listing id {listing_id}")
        listings.append(AllowlistedListing(listing_id, category_id, name))

    return Allowlist(listings=tuple(listings), fence_categories=fence_categories)


def _category(row: Mapping[str, Any]) -> int | None:
    # OLX sends category_id as a number, but compare as ints to be safe ("876" == 876).
    try:
        return int(row.get("category_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def check_seen(
    allowlist: Allowlist, seen: Mapping[int, Mapping[str, Any]]
) -> tuple[list[AllowlistWarning], list[int]]:
    """Compare the allowlist with the rows the category sync saw (listing id -> row).

    Returns the warnings and the ids the sync didn't return (the caller asks OLX about
    those). A listing the sync returned is synced, whatever its category: in its recorded
    category it is silent; anywhere else it gets an INFO line (in a fence category,
    "allowlist entry redundant", in Tomislav's words). The one warning here is a synced
    row with no usable category_id: recorded as "recheck_failed" (never sent to Toni),
    never as a move.
    """
    warnings: list[AllowlistWarning] = []
    missing: list[int] = []
    for entry in allowlist.listings:
        row = seen.get(entry.id)
        if row is None:
            missing.append(entry.id)
            continue
        category = _category(row)
        if category == entry.category_id:
            continue
        if category is None:
            warnings.append(
                AllowlistWarning(
                    listing_id=entry.id,
                    name=entry.name,
                    reason="recheck_failed",
                    expected_category_id=entry.category_id,
                    error="no_category_id",
                )
            )
        elif category in allowlist.fence_categories:
            # Wording agreed with Tomislav (27 Nov 2026).
            logger.info(
                "%d now found via category %s; allowlist entry redundant",
                entry.id,
                allowlist.fence_categories[category],
                extra={
                    "event": "allowlist_listing_in_fence_category",
                    "listing_id": entry.id,
                    "category_id": category,
                    "expected_category_id": entry.category_id,
                },
            )
        else:
            logger.info(
                "%d now found via category %d (allowlisted in %d); still synced",
                entry.id,
                category,
                entry.category_id,
                extra={
                    "event": "allowlist_listing_in_other_category",
                    "listing_id": entry.id,
                    "category_id": category,
                    "expected_category_id": entry.category_id,
                },
            )
    return warnings, missing


__all__ = [
    "DEFAULT_PATH",
    "Allowlist",
    "AllowlistReason",
    "AllowlistWarning",
    "AllowlistedListing",
    "check_seen",
    "load_allowlist",
]
