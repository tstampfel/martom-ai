"""Toni's report: grouping by priority, listing links, and safe text. No database."""

from datetime import UTC, date, datetime

from martom.review.report import (
    ReportIssue,
    ReportListing,
    format_report,
    group_by_priority,
    listing_url,
    report_json,
)

SEEN = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 27)


def issue(
    issue_id: int,
    priority: str,
    message: str = "Poruka.",
    listings: tuple[ReportListing, ...] = (),
    kind: str = "used_state",
) -> ReportIssue:
    return ReportIssue(
        id=issue_id,
        kind=kind,
        priority=priority,  # type: ignore[arg-type]
        message=message,
        listings=listings,
        first_seen_at=SEEN,
    )


def panel(listing_id: int, title: str = "PANELNA OGRADA") -> ReportListing:
    return ReportListing(
        id=listing_id, title=title, url=listing_url(f"panelna-ograda-{listing_id}")
    )


def test_listing_url_uses_the_martom_shop_and_the_slug() -> None:
    assert (
        listing_url("panelna-ograda-153-59473597")
        == "https://martom.olx.ba/artikal/panelna-ograda-153-59473597"
    )


def test_listing_url_is_none_without_a_slug() -> None:
    # olx.ba/artikal/{id} does not open, so there is no fallback link.
    assert listing_url(None) is None
    assert listing_url("") is None


def test_listing_url_escapes_characters_that_would_change_the_url() -> None:
    assert listing_url("a/b?c#d 1") == "https://martom.olx.ba/artikal/a%2Fb%3Fc%23d%201"


def test_group_by_priority_orders_high_medium_low_and_keeps_empty_groups() -> None:
    groups = group_by_priority([issue(1, "low"), issue(2, "high"), issue(3, "high")])

    assert list(groups) == ["high", "medium", "low"]
    assert [i.id for i in groups["high"]] == [2, 3]
    assert groups["medium"] == []
    assert [i.id for i in groups["low"]] == [1]


def test_report_is_grouped_by_priority_in_bosnian_with_links() -> None:
    issues = [
        issue(1, "medium", "Oglas 2: stanje je „Korišteno“.", (panel(2),)),
        issue(2, "high", "Oglas 1: visina 2500CM nije moguća.", (panel(1, "PANEL 153"),)),
        issue(3, "low", "Oglas 3: tipfeler „ograada“.", (panel(3),)),
    ]

    text = format_report(issues, today=TODAY)

    high = text.index("VISOK PRIORITET (1)")
    medium = text.index("SREDNJI PRIORITET (1)")
    low = text.index("NIZAK PRIORITET (1)")
    assert high < medium < low
    assert text.index("visina 2500CM") > high
    assert "https://martom.olx.ba/artikal/panelna-ograda-1" in text
    assert "PANEL 153" in text
    assert "27.11.2026." in text
    assert "Otvorenih problema: 3" in text
    assert "olx.ba/artikal/1\n" not in text


def test_report_skips_empty_priority_groups() -> None:
    text = format_report([issue(1, "high", listings=(panel(1),))], today=TODAY)

    assert "VISOK PRIORITET (1)" in text
    assert "SREDNJI PRIORITET" not in text
    assert "NIZAK PRIORITET" not in text


def test_report_lists_every_listing_of_a_duplicate() -> None:
    dup = issue(
        1, "high", "Isti proizvod je u 2 oglasa.", (panel(5), panel(9)), "duplicate_listing"
    )

    text = format_report([dup], today=TODAY)

    assert "https://martom.olx.ba/artikal/panelna-ograda-5" in text
    assert "https://martom.olx.ba/artikal/panelna-ograda-9" in text


def test_report_says_when_a_listing_has_no_link() -> None:
    no_slug = ReportListing(id=7, title="ŽICA", url=None)

    text = format_report([issue(1, "high", listings=(no_slug,))], today=TODAY)

    assert "ŽICA (oglas 7, bez linka)" in text


def test_issue_without_a_listing_has_no_link_line() -> None:
    stale = issue(1, "high", "Cijena „Anker M8x70“ nije potvrđena.", kind="manual_product_stale")

    text = format_report([stale], today=TODAY)

    assert text.rstrip().endswith("1. Cijena „Anker M8x70“ nije potvrđena.")
    assert "http" not in text


def test_empty_report_says_there_is_nothing_to_fix() -> None:
    text = format_report([], today=TODAY)

    assert "Nema otvorenih problema" in text


def test_listing_text_cannot_inject_terminal_control_characters() -> None:
    # Listing and message text are data: an ANSI escape or a stray newline from a listing
    # must not recolour Toni's terminal or forge an extra report line.
    evil = issue(
        1,
        "high",
        "Oglas 1: \x1b[2Jtekst\nVISOK PRIORITET (99)",
        (ReportListing(id=1, title="NASLOV\r\x1b[31m", url=listing_url("n-1")),),
    )

    text = format_report([evil], today=TODAY)

    assert "\x1b" not in text
    assert "\r" not in text
    assert "VISOK PRIORITET (99)" not in text.splitlines()


def test_report_json_groups_issues_by_priority() -> None:
    issues = [issue(1, "low", listings=(panel(3),)), issue(2, "high", listings=(panel(1),))]

    body = report_json(issues)

    assert body["total"] == 2
    assert body["counts"] == {"high": 1, "medium": 0, "low": 1}
    assert list(body["issues"]) == ["high", "medium", "low"]
    first = body["issues"]["high"][0]
    assert first == {
        "id": 2,
        "kind": "used_state",
        "priority": "high",
        "message": "Poruka.",
        "listings": [
            {
                "id": 1,
                "title": "PANELNA OGRADA",
                "url": "https://martom.olx.ba/artikal/panelna-ograda-1",
            }
        ],
        "first_seen_at": "2026-11-26T12:00:00+00:00",
    }
