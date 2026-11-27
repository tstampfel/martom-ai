"""`run_review_checks` against a real Postgres: what lands in `review_issue`, and reruns.

A throwaway database per test (see conftest.py); no model and no network.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path

import psycopg
import pytest

from martom.bom.pricing import PriceMap
from martom.migrate import run_migrations
from martom.review.job import run_review_checks

pytestmark = pytest.mark.anyio

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
TODAY = date(2026, 11, 27)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def empty_price_map(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """These databases hold none of the listings the BOM price map points at, so the
    shipped map would raise a bom_price_missing issue per entry. The tests that check the
    price map itself opt out by requesting the `shipped_price_map` fixture."""
    if "shipped_price_map" not in request.fixturenames:
        monkeypatch.setattr(
            "martom.review.job.load_price_map",
            lambda path: PriceMap(status="draft", entries={}, unmapped={}),
        )


@pytest.fixture
def shipped_price_map() -> None:
    """Request it to run the review with src/martom/bom/price_map.toml."""


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[psycopg.AsyncConnection]:
    run_migrations(database_url, MIGRATIONS)
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as c:
        yield c


@pytest.fixture
def manual_toml(tmp_path: Path) -> Path:
    path = tmp_path / "manual_products.toml"
    path.write_text(
        '[anchor_m8x70]\nname = "Anker M8x70"\nprice_km = "0.80"\n'
        'source = "Toni 26.11.2026"\nlast_confirmed = 2026-11-26\n',
        encoding="utf-8",
    )
    return path


async def add_listing(
    conn: psycopg.AsyncConnection,
    listing_id: int,
    *,
    title: str = "PANELNA OGRADA",
    state: str = "new",
    images: list[str] | None = None,
    active: bool = True,
    variants: Sequence[tuple[str, str | None, str | None, str | None]] = (),
) -> None:
    """A listing with an extracted product; variants are (label, height, length, price)."""
    summary = {"images": ["https://example.test/a.jpg"] if images is None else images}
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, description_text, price,"
        " display_price, price_by_agreement, state, attributes, detail_hash, active)"
        " VALUES (%s, %s, %s, %s, 'Opis.', 10, '10 KM', false, %s, '[]', 'h', %s)",
        (listing_id, f"panel-{listing_id}", title, json.dumps(summary), state, active),
    )
    await conn.execute(
        "INSERT INTO product (listing_id, detail_hash, product_type, extraction, model,"
        " prompt_version, search_text) VALUES (%s, 'h', 'panelna ograda', '{}', 'm', 'v', 't')",
        (listing_id,),
    )
    for position, (label, height, length, price) in enumerate(variants):
        await conn.execute(
            "INSERT INTO product_variant (listing_id, position, label, height, length,"
            " price_text, price) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                listing_id,
                position,
                label,
                height,
                length,
                None if price is None else f"{price} KM",
                None if price is None else price.replace(",", "."),
            ),
        )


async def issues(conn: psycopg.AsyncConnection) -> list[tuple]:
    cursor = await conn.execute(
        "SELECT kind, listing_id, priority, status FROM review_issue ORDER BY kind, listing_id"
    )
    return await cursor.fetchall()


PANEL_WITH_PROBLEMS = [
    ("Panel 153 x 250 cm", "153", "250 cm", "53,00"),
    ("PANEL 153 X 2500CM", "153", "2500CM", "53,00"),
    ("Panel 173 x 250 cm", "173", "250 cm", None),
]


async def test_saves_issues_with_bosnian_messages(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, variants=PANEL_WITH_PROBLEMS, state="used", images=[])

    stats = await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [
        ("height_without_price", 1, "high", "open"),
        ("implausible_dimension", 1, "high", "open"),
        ("listing_price_mismatch", 1, "medium", "open"),
        ("missing_photos", 1, "medium", "open"),
        ("used_state", 1, "medium", "open"),
    ]
    assert (stats.raised, stats.new, stats.resolved) == (5, 5, 0)
    cursor = await conn.execute(
        "SELECT message, details FROM review_issue WHERE kind = 'implausible_dimension'"
    )
    message, details = await cursor.fetchone()  # type: ignore[misc]
    assert "2500CM" in message and message.startswith("Oglas 1:")
    assert details["value"] == "2500CM"


async def test_rerun_does_not_duplicate(conn: psycopg.AsyncConnection, manual_toml: Path) -> None:
    await add_listing(conn, 1, variants=PANEL_WITH_PROBLEMS)
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    first = await issues(conn)

    stats = await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == first
    assert stats.new == 0
    cursor = await conn.execute(
        "SELECT count(*) FROM review_issue WHERE last_seen_at >= first_seen_at"
    )
    assert (await cursor.fetchone())[0] == len(first)  # type: ignore[index]


async def test_fixed_problem_is_resolved_and_reopens_if_it_comes_back(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, state="used")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("used_state", 1, "medium", "open")]

    await conn.execute("UPDATE olx_listing SET state = 'new' WHERE id = 1")
    stats = await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("used_state", 1, "medium", "resolved")]
    assert stats.resolved == 1

    await conn.execute("UPDATE olx_listing SET state = 'used' WHERE id = 1")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("used_state", 1, "medium", "open")]
    cursor = await conn.execute("SELECT resolved_at FROM review_issue")
    assert (await cursor.fetchone())[0] is None  # type: ignore[index]


async def test_ignored_issue_stays_ignored(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, state="used")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    await conn.execute("UPDATE review_issue SET status = 'ignored'")

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("used_state", 1, "medium", "ignored")]

    # Gone and back: Toni already decided to ignore it, so it stays ignored.
    await conn.execute("UPDATE olx_listing SET state = 'new' WHERE id = 1")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("used_state", 1, "medium", "ignored")]


async def test_inactive_listings_are_not_checked(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, state="used", active=False)
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == []


async def test_listing_without_product_is_still_checked(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, state)"
        " VALUES (7, 'x-7', 'BRUSILICA', '{\"image\": null, \"images\": []}', 'used')"
    )
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [
        ("missing_photos", 7, "medium", "open"),
        ("used_state", 7, "medium", "open"),
    ]


async def test_duplicate_titles_across_listings(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, title="PANEL 153")
    await add_listing(conn, 2, title="Panel 153")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == [("duplicate_listing", 1, "medium", "open")]


async def test_stale_manual_product(conn: psycopg.AsyncConnection, manual_toml: Path) -> None:
    await run_review_checks(conn, today=date(2027, 3, 1), manual_products_path=manual_toml)
    assert await issues(conn) == [("manual_product_stale", None, "high", "open")]


async def test_shipped_manual_products_file_is_read_by_default(
    conn: psycopg.AsyncConnection,
) -> None:
    # src/martom/bom/manual_products.toml as on main: anchor_m8x70, confirmed 2026-11-26.
    await run_review_checks(conn, today=date(2026, 11, 27))
    assert await issues(conn) == []
    await run_review_checks(conn, today=date(2027, 2, 25))
    assert await issues(conn) == [("manual_product_stale", None, "high", "open")]


@pytest.mark.usefixtures("shipped_price_map")
async def test_shipped_price_map_is_read_by_default(conn: psycopg.AsyncConnection) -> None:
    # No mapped listing is in this database, so every OLX entry of the shipped map is
    # "cijena na upit"; the manual M8x70 anchor still prices from manual_products.toml.
    await run_review_checks(conn, today=TODAY)
    found = await issues(conn)
    assert found
    assert {row[0] for row in found} == {"bom_price_missing"}
    assert {row[1] for row in found} == {None}  # listings not active: no listing id


@pytest.mark.usefixtures("shipped_price_map")
async def test_a_broken_price_map_skips_only_the_bom_check(
    conn: psycopg.AsyncConnection,
    tmp_path: Path,
    manual_toml: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # R1-10: the other checks still raise and resolve issues; the error is logged.
    await add_listing(conn, 1, state="used")
    broken = tmp_path / "price_map.toml"
    broken.write_text('status = "maybe"\n', encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="martom.review.job"):
        await run_review_checks(
            conn, today=TODAY, manual_products_path=manual_toml, price_map_path=broken
        )
    assert await issues(conn) == [("used_state", 1, "medium", "open")]
    assert any(r.message == "price map invalid, BOM price check skipped" for r in caplog.records)


async def test_issue_of_a_listing_that_goes_inactive_is_resolved_with_the_reason(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, state="used", title="PANEL A")
    await add_listing(conn, 2, state="used", title="PANEL B")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    await conn.execute("UPDATE olx_listing SET active = false WHERE id = 1")
    await conn.execute("UPDATE olx_listing SET state = 'new' WHERE id = 2")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    cursor = await conn.execute(
        "SELECT listing_id, status, details ->> 'resolved_because' FROM review_issue"
        " ORDER BY listing_id"
    )
    assert await cursor.fetchall() == [
        (1, "resolved", "listing_inactive"),
        (2, "resolved", "not_found"),
    ]

    # Back again: open, and the reason is gone.
    await conn.execute("UPDATE olx_listing SET active = true WHERE id = 1")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    cursor = await conn.execute(
        "SELECT status, details ? 'resolved_because' FROM review_issue WHERE listing_id = 1"
    )
    assert await cursor.fetchone() == ("open", False)


async def test_single_image_counts_when_the_image_list_is_empty(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary, state) VALUES"
        " (7, 'x-7', 'BRUSILICA', '{\"image\": \"https://example.test/a.jpg\", \"images\": []}',"
        " 'new')"
    )
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    assert await issues(conn) == []


async def test_raised_counts_each_problem_once(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    # Two same-size variants without a price: two problems, two rows, counted once each.
    same = [("Panel 153 x 250 cm", "153", "250 cm", "53,00")] + [
        (f"Panel 173 x 250 cm ({n})", "173", "250 cm", None) for n in (1, 2)
    ]
    await add_listing(conn, 1, variants=same, images=["a"])
    stats = await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    cursor = await conn.execute(
        "SELECT count(*) FROM review_issue WHERE kind = 'height_without_price'"
    )
    assert (await cursor.fetchone())[0] == 2  # type: ignore[index]
    cursor = await conn.execute("SELECT count(*) FROM review_issue")
    assert stats.raised == (await cursor.fetchone())[0]  # type: ignore[index]


async def test_save_issues_never_drops_an_issue_with_a_colliding_fingerprint(
    conn: psycopg.AsyncConnection,
) -> None:
    from martom.review.checks import ReviewIssue
    from martom.review.store import save_issues

    await conn.execute(
        "INSERT INTO olx_listing (id, slug, title, summary) VALUES (1, 'x-1', 'X', '{}')"
    )
    first = ReviewIssue("typo", 1, "low", "Prva", {"n": 1}, key="k")
    second = ReviewIssue("typo", 1, "low", "Druga", {"n": 2}, key="k")
    assert first.fingerprint == second.fingerprint

    assert (await save_issues(conn, [first, second]))[:2] == (2, 2)
    # Stable: the same input on the next run writes the same two rows, nothing new.
    assert (await save_issues(conn, [first, second]))[:2] == (2, 0)
    cursor = await conn.execute("SELECT message FROM review_issue ORDER BY message")
    assert await cursor.fetchall() == [("Druga",), ("Prva",)]


async def test_duplicate_resolved_because_a_higher_copy_went_inactive(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_listing(conn, 1, title="PANEL 153")
    await add_listing(conn, 2, title="PANEL 153")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    await conn.execute("UPDATE olx_listing SET active = false WHERE id = 2")
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    cursor = await conn.execute(
        "SELECT status, details ->> 'resolved_because' FROM review_issue"
        " WHERE kind = 'duplicate_listing'"
    )
    assert await cursor.fetchone() == ("resolved", "listing_inactive")


# --- allowlist warnings from the sync (#45) --------------------------------------------------


async def add_sync_run(
    conn: psycopg.AsyncConnection, warnings: list[dict] | None, *, coverage: float = 1.0
) -> None:
    """A finished nightly run with `allowlist_warnings` (None: the guard didn't run).

    `coverage` below 1 makes it a partial run (the sync didn't page every listing).
    """
    status = "succeeded" if coverage >= 1 else "partial"
    await conn.execute(
        "INSERT INTO sync_run (kind, status, finished_at, coverage, allowlist_warnings)"
        " VALUES ('nightly', %s, now(), %s, %s)",
        (status, coverage, None if warnings is None else json.dumps(warnings)),
    )


GONE = {
    "listing_id": 18129143,
    "name": "Španer (zatezač za žicu)",
    "reason": "404",
    "expected_category_id": 1126,
    "category_id": None,
    "error": None,
}


async def test_allowlist_warning_of_the_latest_sync_raises_an_issue_for_toni(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [GONE])

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_gone", None, "high", "open")]


async def test_allowlist_issue_is_resolved_when_a_later_sync_finds_nothing(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [GONE])
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    await add_sync_run(conn, [])

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_gone", None, "high", "resolved")]


async def test_a_sync_run_that_never_checked_the_allowlist_keeps_the_issue_open(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [GONE])
    await add_sync_run(conn, None)  # say, a run that broke before the guard

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_gone", None, "high", "open")]


async def test_allowlisted_listing_the_sync_missed_raises_an_issue_for_toni(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [GONE | {"reason": "not_seen"}])

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_not_synced", None, "medium", "open")]


NOT_SEEN = GONE | {"reason": "not_seen"}


async def test_not_seen_from_a_partial_run_raises_no_issue_for_toni(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    # R1A-1: below 100% coverage the sync may simply not have reached the listing's page,
    # so "in no category" isn't proven (the same rule as deactivation).
    await add_sync_run(conn, [NOT_SEEN], coverage=0.98)

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == []


async def test_gone_from_a_partial_run_still_raises_an_issue(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    # A 404 rests on a GET that answered, not on the paging, so coverage doesn't matter.
    await add_sync_run(conn, [GONE], coverage=0.98)

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_gone", None, "high", "open")]


async def test_open_not_synced_issue_stays_open_after_a_partial_run(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [NOT_SEEN])  # 100% coverage: the issue is raised
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    # A partial run that didn't find the listing either way says nothing about it.
    await add_sync_run(conn, [], coverage=0.98)

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_not_synced", None, "medium", "open")]


async def test_not_synced_issue_is_resolved_by_the_next_full_coverage_run(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [NOT_SEEN])
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    await add_sync_run(conn, [], coverage=0.98)
    await add_sync_run(conn, [])  # 100% coverage, the listing is back

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_not_synced", None, "medium", "resolved")]


async def test_gone_issue_follows_the_latest_run_even_when_partial(
    conn: psycopg.AsyncConnection, manual_toml: Path
) -> None:
    await add_sync_run(conn, [GONE])
    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)
    # A partial run that saw the listing (or got a 200 for it) has the evidence.
    await add_sync_run(conn, [], coverage=0.98)

    await run_review_checks(conn, today=TODAY, manual_products_path=manual_toml)

    assert await issues(conn) == [("allowlist_listing_gone", None, "high", "resolved")]
