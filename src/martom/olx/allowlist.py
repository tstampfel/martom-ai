"""The allowlist guard (#45): fence listings outside "Ograde" must stay synced.

`allowlist.toml` (next to this file) lists the listings and the category each was
allowlisted in. This module holds the plain parts: loading the file and comparing it to
the rows the category sync saw (`check_seen`). The sync job (`martom.olx.job`) asks OLX
about the ids the sync didn't see and records every warning on the `sync_run` row.
"""

import logging
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).with_name("allowlist.toml")

# moved:          seen, but outside its recorded category and outside the covered ones
# 404 / inactive / not_martom: not seen, and GET /listings/{id} says it is gone
# not_seen:       not seen, though GET /listings/{id} says it is active and Martom's
#                 (the category sync missed it: the guard's main reason to exist)
# recheck_failed: not seen, and GET /listings/{id} failed (network, timeout, 5xx, ...)
type AllowlistReason = Literal[
    "moved", "404", "inactive", "not_martom", "not_seen", "recheck_failed"
]


@dataclass(frozen=True)
class AllowlistedListing:
    id: int
    category_id: int  # the OLX category it was allowlisted in
    name: str  # our name for it (messages), never listing text


@dataclass(frozen=True)
class Allowlist:
    listings: tuple[AllowlistedListing, ...]
    # Category id -> name ("Ograde"): the categories the sync covers for the fence.
    covered_categories: Mapping[int, str]


@dataclass(frozen=True)
class AllowlistWarning:
    """One problem with one allowlisted listing; stored as JSON in the `sync_run` row."""

    listing_id: int
    name: str
    reason: AllowlistReason
    expected_category_id: int
    category_id: int | None = None  # where the sync saw it; None when it wasn't seen
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

    covered = data.get("covered_categories")
    if not isinstance(covered, dict) or not covered:
        raise ValueError("allowlist: [covered_categories] must list at least one category")
    covered_categories: dict[int, str] = {}
    for key, name in covered.items():
        # TOML keys are always strings ("881"), so turn them back into ids.
        if not key.isdigit() or not isinstance(name, str) or not name.strip():
            raise ValueError('allowlist: [covered_categories] entries are <id> = "<name>"')
        covered_categories[int(key)] = name

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

    return Allowlist(listings=tuple(listings), covered_categories=covered_categories)


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

    Returns the "moved" warnings and the ids that weren't seen at all (the caller asks
    OLX about those). A listing seen in a covered category ("Ograde") is the good case: it
    is logged at info level, so Tomislav knows the entry is redundant, and raises nothing.
    Only a listing outside its recorded category and every covered one warns.
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
        if category is not None and category in allowlist.covered_categories:
            # Wording agreed with Tomislav (27 Nov 2026).
            logger.info(
                "%d now found via category %s; allowlist entry redundant",
                entry.id,
                allowlist.covered_categories[category],
                extra={
                    "event": "allowlist_listing_in_covered_category",
                    "listing_id": entry.id,
                    "category_id": category,
                    "expected_category_id": entry.category_id,
                },
            )
            continue
        warnings.append(
            AllowlistWarning(
                listing_id=entry.id,
                name=entry.name,
                reason="moved",
                expected_category_id=entry.category_id,
                category_id=category,
            )
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
