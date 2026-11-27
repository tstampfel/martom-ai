"""`load_open_issues` against a real Postgres: which issues reach Toni's report.

A throwaway database per test (see conftest.py); no model and no network.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from martom.migrate import run_migrations
from martom.review.report import load_open_issues

pytestmark = pytest.mark.anyio

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[psycopg.AsyncConnection]:
    run_migrations(database_url, MIGRATIONS)
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as c:
        yield c


async def add_listing(
    conn: psycopg.AsyncConnection,
    listing_id: int,
    *,
    slug: str | None = "",
    active: bool = True,
    title: str = "PANELNA OGRADA",
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, active) VALUES (%s, %s, %s, '{}', %s)",
        (listing_id, f"panelna-ograda-{listing_id}" if slug == "" else slug, title, active),
    )


async def add_issue(
    conn: psycopg.AsyncConnection,
    fingerprint: str,
    listing_id: int | None,
    *,
    priority: str = "high",
    status: str = "open",
    kind: str = "used_state",
    details: dict[str, object] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO review_issue (fingerprint, kind, listing_id, priority, message, details,"
        " status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            fingerprint,
            kind,
            listing_id,
            priority,
            f"Poruka {fingerprint}.",
            json.dumps(details or {}),
            status,
        ),
    )


async def test_only_open_issues_are_reported(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)
    await add_issue(conn, "open", 1)
    await add_issue(conn, "resolved", 1, status="resolved")
    await add_issue(conn, "ignored", 1, status="ignored")

    issues = await load_open_issues(conn)

    assert [i.message for i in issues] == ["Poruka open."]


async def test_issues_of_inactive_listings_are_left_out(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2, active=False)
    await add_issue(conn, "active", 1)
    await add_issue(conn, "sold", 2)

    issues = await load_open_issues(conn)

    assert [i.message for i in issues] == ["Poruka active."]


async def test_issue_without_a_listing_is_reported(conn: psycopg.AsyncConnection) -> None:
    await add_issue(conn, "stale", None, kind="manual_product_stale")

    issues = await load_open_issues(conn)

    assert [(i.kind, i.listings) for i in issues] == [("manual_product_stale", ())]


async def test_issues_are_ordered_by_priority_and_carry_links(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_listing(conn, 1)
    await add_listing(conn, 2, title="ŽICA")
    await add_issue(conn, "low", 1, priority="low")
    await add_issue(conn, "high", 2, priority="high")
    await add_issue(conn, "medium", 1, priority="medium")

    issues = await load_open_issues(conn)

    assert [i.priority for i in issues] == ["high", "medium", "low"]
    listing = issues[0].listings[0]
    assert (listing.id, listing.title, listing.url) == (
        2,
        "ŽICA",
        "https://martom.olx.ba/artikal/panelna-ograda-2",
    )


async def test_listing_without_a_slug_has_no_link(conn: psycopg.AsyncConnection) -> None:
    await add_listing(conn, 1, slug=None)
    await add_issue(conn, "a", 1)

    [issue] = await load_open_issues(conn)

    assert issue.listings[0].url is None


async def test_duplicate_links_every_active_listing_of_the_group(
    conn: psycopg.AsyncConnection,
) -> None:
    for listing_id, active in [(1, False), (2, True), (3, True)]:
        await add_listing(conn, listing_id, active=active)
    await add_issue(conn, "dup", 1, kind="duplicate_listing", details={"listing_ids": [1, 2, 3]})

    [issue] = await load_open_issues(conn)

    assert [listing.id for listing in issue.listings] == [2, 3]
