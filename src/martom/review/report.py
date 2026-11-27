"""Toni's report: the open review issues, grouped by priority, with links to fix them on OLX.

`martom report` prints `format_report(...)` and `GET /admin/review-issues` returns
`report_json(...)`. Both read the same `load_open_issues`. The report is derived data:
it is printed or served, never written into the repo.

Messages and listing titles come from listings, so they are data, not instructions: the
text report strips control characters (no ANSI escapes or forged lines in Toni's terminal),
and the JSON is escaped by the JSON encoder.
"""

# The Bosnian text uses typographic dashes on purpose.
# ruff: noqa: RUF001

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, get_args
from urllib.parse import quote

import psycopg

from martom.review.checks import Priority

# The shop's own OLX subdomain; olx.ba/artikal/{id} does not open (CLAUDE.md).
LISTING_URL_BASE = "https://martom.olx.ba/artikal/"

PRIORITIES: tuple[Priority, ...] = get_args(Priority.__value__)  # ("high", "medium", "low")
_HEADINGS: dict[Priority, str] = {
    "high": "VISOK PRIORITET",
    "medium": "SREDNJI PRIORITET",
    "low": "NIZAK PRIORITET",
}
_COUNT_NAMES: dict[Priority, str] = {"high": "visok", "medium": "srednji", "low": "nizak"}

# Characters that could change how the report looks rather than what it says: control
# characters (Cc: newline, carriage return, ESC, ...), format characters (Cf: bidi
# overrides and isolates, zero-width marks) and Unicode line/paragraph separators (Zl, Zp).
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def listing_url(slug: str | None) -> str | None:
    """https://martom.olx.ba/artikal/{slug}, or None without a slug (there is no fallback).

    The slug is percent-encoded, so a "/", "?" or "#" in it can't point the link elsewhere.
    """
    if not slug:
        return None
    return LISTING_URL_BASE + quote(slug, safe="-._~")


@dataclass(frozen=True)
class ReportListing:
    id: int
    title: str
    url: str | None  # None when the listing has no slug yet


@dataclass(frozen=True)
class ReportIssue:
    id: int
    kind: str
    priority: Priority
    message: str  # Bosnian, written by the check; may quote listing text
    # The listings to fix: one, all active listings of a duplicate group, or none for an
    # issue that isn't about a listing (a stale manual_products.toml price).
    listings: tuple[ReportListing, ...]
    first_seen_at: datetime


# Open issues only (resolved and ignored are left out), highest priority first.
_OPEN_ISSUES = """
SELECT id, kind, priority, message, listing_id, details, first_seen_at
FROM review_issue
WHERE status = 'open'
ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
         listing_id NULLS FIRST, id
"""

_ACTIVE_LISTINGS = """
SELECT id, slug, title FROM active_olx_listing WHERE id = ANY(%s)
"""


def _listing_ids(listing_id: int | None, details: object) -> list[int]:
    """The issue's listing, or for a duplicate every listing in details.listing_ids."""
    ids = details.get("listing_ids") if isinstance(details, dict) else None
    if isinstance(ids, list) and ids:
        return [int(i) for i in ids]
    return [] if listing_id is None else [listing_id]


async def load_open_issues(conn: psycopg.AsyncConnection) -> list[ReportIssue]:
    """Open issues whose listing is still active (plus issues not about a listing).

    Reads listings from `active_olx_listing` only. An issue whose listings are all inactive
    is left out: the next review run resolves it as 'listing_inactive'.
    """
    cursor = await conn.execute(_OPEN_ISSUES)
    rows = await cursor.fetchall()
    wanted = {i for row in rows for i in _listing_ids(row[4], row[5])}

    cursor = await conn.execute(_ACTIVE_LISTINGS, (sorted(wanted),))
    active = {
        listing_id: ReportListing(listing_id, title or "", listing_url(slug))
        for listing_id, slug, title in await cursor.fetchall()
    }

    issues = []
    for issue_id, kind, priority, message, listing_id, details, first_seen_at in rows:
        ids = _listing_ids(listing_id, details)
        listings = tuple(active[i] for i in ids if i in active)
        if ids and not listings:
            continue
        issues.append(ReportIssue(issue_id, kind, priority, message, listings, first_seen_at))
    return issues


def group_by_priority(issues: Iterable[ReportIssue]) -> dict[Priority, list[ReportIssue]]:
    """{"high": [...], "medium": [...], "low": [...]}, in that order, keeping input order."""
    groups: dict[Priority, list[ReportIssue]] = {priority: [] for priority in PRIORITIES}
    for issue in issues:
        groups[issue.priority].append(issue)
    return groups


def _clean(text: str) -> str:
    """One line of plain text: each run of unsafe characters becomes a single space."""
    safe = "".join(" " if unicodedata.category(c) in _UNSAFE_CATEGORIES else c for c in text)
    return re.sub(r" {2,}", " ", safe).strip()


def _listing_line(listing: ReportListing) -> str:
    title = _clean(listing.title) or f"Oglas {listing.id}"
    if listing.url is None:
        return f"{title} (oglas {listing.id}, bez linka)"
    return f"{title} – {listing.url}"


def format_report(issues: Sequence[ReportIssue], *, today: date) -> str:
    """The Bosnian message for Toni, grouped by priority (empty groups are left out)."""
    lines = [f"Martom – oglasi za ispravku na OLX-u ({today:%d.%m.%Y}.)", ""]
    if not issues:
        lines.append("Nema otvorenih problema. Svi oglasi su u redu.")
        return "\n".join(lines) + "\n"

    groups = group_by_priority(issues)
    counts = ", ".join(
        f"{_COUNT_NAMES[priority]} prioritet: {len(group)}"
        for priority, group in groups.items()
        if group
    )
    lines.append(f"Otvorenih problema: {len(issues)} ({counts})")
    for priority, group in groups.items():
        if not group:
            continue
        lines += ["", f"{_HEADINGS[priority]} ({len(group)})"]
        for number, issue in enumerate(group, start=1):
            lines.append(f"{number}. {_clean(issue.message)}")
            lines += [f"   {_listing_line(listing)}" for listing in issue.listings]
    return "\n".join(lines) + "\n"


def report_json(issues: Sequence[ReportIssue]) -> dict[str, Any]:
    """The same report as JSON for GET /admin/review-issues."""
    groups = group_by_priority(issues)
    return {
        "total": len(issues),
        "counts": {priority: len(group) for priority, group in groups.items()},
        "issues": {
            priority: [
                {
                    "id": issue.id,
                    "kind": issue.kind,
                    "priority": issue.priority,
                    "message": issue.message,
                    "listings": [
                        {"id": listing.id, "title": listing.title, "url": listing.url}
                        for listing in issue.listings
                    ],
                    "first_seen_at": issue.first_seen_at.isoformat(),
                }
                for issue in group
            ]
            for priority, group in groups.items()
        },
    }
