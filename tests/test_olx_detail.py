"""Tests for the detail fetch (M1-3): description cleanup, change-detection hashes, re-fetch.

Test data: `tests/fixtures/olx/listing_details.json` holds six real GET /listings/{id}
responses recorded on 26 Nov 2026 (trimmed to the fields we read) and one real summary row.
They cover the shop's three footer styles: the new long footer (address, contact, PIKA,
socials), the short emoji footer, and an old description where the footer sits in the
middle with panel sizes after it and a "NAŠE REFERENCE" list at the end.
"""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from martom.olx.client import OlxApiError, OlxClient, OlxRateLimitAbortError
from martom.olx.detail import (
    DetailFetchAbortedError,
    ListingDetail,
    clean_description,
    description_to_text,
    fetch_changed_details,
    needs_detail_fetch,
    strip_boilerplate,
    summary_hash,
)

FIXTURE = Path(__file__).parent / "fixtures" / "olx" / "listing_details.json"
SHOP_PHONE = "036 651 427"


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def detail_body(recorded: dict[str, Any], listing_id: int) -> dict[str, Any]:
    return copy.deepcopy(recorded["details"][str(listing_id)])


def cleaned(recorded: dict[str, Any], listing_id: int) -> str:
    return clean_description(detail_body(recorded, listing_id)["additional"]["description"])


# --- HTML to text -----------------------------------------------------------------------


def test_block_tags_become_lines_and_inline_tags_do_not_split_words() -> None:
    html = "<h2>Naslov</h2><p>Za ogradu nudimo i<a href='x'> trake</a> <strong>RAL</strong>"
    html += " 6005</p><ul><li>prvi</li><li>drugi</li></ul><p>a<br>b</p>"
    assert (
        description_to_text(html) == "Naslov\nZa ogradu nudimo i trake RAL 6005\nprvi\ndrugi\na\nb"
    )


def test_entities_nbsp_and_bom_are_decoded_and_whitespace_collapsed() -> None:
    html = "<p>&amp; Ø&nbsp;4,20 &lt;mm&gt;  \ufeff👉 x</p><p><br></p><p><br></p><p>y</p>"
    assert description_to_text(html) == "& Ø 4,20 <mm> 👉 x\n\ny"


def test_script_and_style_content_is_dropped() -> None:
    assert description_to_text("<p>a</p><script>alert(1)</script><style>p{}</style>") == "a"


def test_empty_or_missing_description_is_empty_text() -> None:
    assert description_to_text(None) == ""
    assert description_to_text("") == ""
    assert description_to_text("<p><br></p>") == ""


def test_text_is_copied_not_fixed() -> None:
    """LaTeX, odd sizes and shouting stay exactly as written (extraction flags them)."""
    html = r"<p>debljine $\varnothing 1,80\text{ mm}$ PANEL 153 X 2500CM!!!</p>"
    assert (
        description_to_text(html) == r"debljine $\varnothing 1,80\text{ mm}$ PANEL 153 X 2500CM!!!"
    )


# --- Boilerplate --------------------------------------------------------------------------


def test_strip_boilerplate_drops_contact_lines_and_keeps_facts() -> None:
    text = "\n".join(
        [
            "KATANAC 50MM - 18,00KM",
            "👉 Dostupno u Željezariji MARTOM d.o.o. Međugorje Tromeđa BB Kružni tok",
            f"📞 {SHOP_PHONE} | 060 000 000",
            "✉️ info@martom-bekaert.com",
            "🛒 Naručite i preko Pika – brza dostava po pristupačnoj cijeni!",  # noqa: RUF001 real text
            "📲 Zapratite nas na društvenim mrežama ovdje",
            "Dostava po dogovoru",
        ]
    )
    assert strip_boilerplate(text) == "KATANAC 50MM - 18,00KM\nDostava po dogovoru"


def test_strip_boilerplate_drops_the_references_list_at_the_end() -> None:
    text = "Stup 830 mm\n\nNAŠE REFERENCE:\n\nMeđunarodni aerodrom Sarajevo\nKONZUM"
    assert strip_boilerplate(text) == "Stup 830 mm"


@pytest.mark.parametrize(
    "line",
    [
        "KATANAC 50MM - 18,00KM, pozovite 060 000 000",
        "Panel 83x200 - 27 KM (slike na www.martom-bekaert.com)",
        "Osobno preuzimanje: rola 50 m - 63 KM",
        "Pogledajte tablicu: stup 155 cm ide 4 kopče, više ovdje",
        "Mreža pletena 050 100 150 cm - 45,00KM",
        "Šifra artikla 063 120 150",
        "Dostupno u Željezariji Martom: panel 153 x 250 cm",
    ],
)
def test_a_line_with_a_product_fact_is_kept_unchanged(line: str) -> None:
    """A boilerplate phrase, link or number on a line never costs us the fact next to it."""
    assert strip_boilerplate(f"Uvod\n{line}") == f"Uvod\n{line}"


def test_a_fact_after_the_references_heading_is_kept() -> None:
    html = "<p>NAŠE REFERENCE: OBI</p><p>KONZUM Mostar</p><p>Panel 153 x 250: 45 KM</p>"
    assert clean_description(html) == "Panel 153 x 250: 45 KM"


def test_table_cells_are_separated() -> None:
    html = (
        "<table><tr><th>Dimenzija</th><th>Cijena</th></tr>"
        "<tr><td>153 x 250</td><td>45,00 KM</td></tr>"
        "<tr>\n  <td>203 x 250</td>\n  <td>85,00 KM</td>\n</tr></table>"
    )
    assert description_to_text(html) == (
        "Dimenzija | Cijena\n153 x 250 | 45,00 KM\n203 x 250 | 85,00 KM"
    )


def test_definition_lists_and_pre_are_blocks() -> None:
    html = "<dl><dt>Visina</dt><dd>153 cm</dd></dl><pre>Ø 4,20 mm</pre>"
    assert description_to_text(html) == "Visina\n153 cm\nØ 4,20 mm"


def test_new_style_footer_is_stripped_and_every_price_line_survives(
    recorded: dict[str, Any],
) -> None:
    text = cleaned(recorded, 63996799)
    for gone in [SHOP_PHONE, "060 000 000", "info@martom-bekaert.com", "PIKA", "Tromeđa BB",
                 "Praktična lokacija", "Osobno preuzimanje"]:  # fmt: skip
        assert gone not in text
    assert text.count("KM / panel") == 10
    assert "Stup H-205 cm sa stopom (za panele visine 203 cm): 31,00 KM / kom" in text
    assert "PVC kopča sa samorezivim vijkom: 1,00 KM / kom" in text
    assert "Za stupove visine 155 cm → idu 4 kopče po stupu" in text
    assert text.startswith("🏡 3D PANELNA OGRADA")


def test_footer_in_the_middle_keeps_the_facts_after_it(recorded: dict[str, Any]) -> None:
    text = cleaned(recorded, 1199211)
    assert SHOP_PHONE not in text and "060 000 000" not in text
    assert "www.martom-bekaert.com" not in text and "facebook.com" not in text
    assert "Dostupno u Željezariji" not in text
    # The panel sizes come after the footer in this old description.
    assert "2500 x 1530 mm" in text
    assert "Stupovi su od profila 50x50mm i sa 4 rupe na pluči" in text
    assert "DOSTUPNE BOJE : MASLINASTO ZELENA RAL 6005; ANTRACIT 7016 I SIVA 7040" in text
    assert "REFERENCE" not in text and "aerodrom" not in text


def test_short_footers_are_stripped(recorded: dict[str, Any]) -> None:
    farmer = cleaned(recorded, 24449780)
    assert "150CM -( 150/12/15) - 130,00KM/ROLA 50M" in farmer
    assert farmer.endswith(
        "Univerzalna primjena – poljoprivreda, šumarstvo, farme i privatna zemljišta."  # noqa: RUF001
    )

    padlock = cleaned(recorded, 36376333)
    assert padlock == "KATANAC DUGI\n50MM - 18,00KM ,\n40MM - 10,00KM I\n30MM IBFM - 6,75KM"

    dog_house = cleaned(recorded, 35990143)
    assert "KUĆICA ZA PSA VELIKA 70X96X96 - 365,00KM" in dog_house
    assert "060 000 000" not in dog_house  # last line: delivery blurb with the phone


def test_latex_description_is_kept_as_written(recorded: dict[str, Any]) -> None:
    text = cleaned(recorded, 54710841)
    assert r"$\varnothing 1,80\text{ mm}$" in text
    assert "Bodljikava žica cinčana 250 m (fi 1,80mm): 58,00 KM" in text
    assert "Šaljemo brzom poštom" not in text and "Facebook | Instagram" not in text


def test_every_recorded_description_loses_the_shop_contact(recorded: dict[str, Any]) -> None:
    for listing_id in recorded["details"]:
        text = cleaned(recorded, int(listing_id))
        assert text, listing_id
        assert SHOP_PHONE not in text, listing_id
        assert "060 000 000" not in text, listing_id
        assert "@martom-bekaert.com" not in text, listing_id


# --- Summary hash -------------------------------------------------------------------------


def test_summary_hash_is_stable_and_ignores_key_order(recorded: dict[str, Any]) -> None:
    row = recorded["summary_row"]
    reordered = dict(reversed(list(row.items())))
    assert summary_hash(row) == summary_hash(copy.deepcopy(row)) == summary_hash(reordered)
    assert len(summary_hash(row)) == 64  # sha256 hex


@pytest.mark.parametrize(
    ("field", "value"),
    [("date", 1), ("score", 9.5), ("sponsored", 0), ("refresh_available", True),
     ("premium_badges", ["x"]), ("labels", ["x"]), ("brand_new_olx_field", 1)],
)  # fmt: skip
def test_summary_hash_ignores_volatile_fields(
    recorded: dict[str, Any], field: str, value: object
) -> None:
    """OLX bumps `date` on every refresh (all recorded rows were dated today), so it must
    not count as a change, or every listing would be re-fetched every night."""
    row = recorded["summary_row"]
    assert summary_hash({**row, field: value}) == summary_hash(row)


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", "PANELNA OGRADA NOVO"), ("price", 99), ("display_price", "99 KM"),
     ("has_discount", True), ("available", True), ("visible", False),
     ("status", "inactive"),
     ("category_id", 1), ("images", []), ("state", "used")],
)  # fmt: skip
def test_summary_hash_changes_when_listing_content_changes(
    recorded: dict[str, Any], field: str, value: object
) -> None:
    row = recorded["summary_row"]
    assert row.get(field) != value  # the recorded row has available=False, visible=True
    assert summary_hash({**row, field: value}) != summary_hash(row)


def test_a_renewal_changes_only_fields_the_summary_hash_leaves_out(
    recorded: dict[str, Any],
) -> None:
    """#179: an OLX renewal ("Obnovi", 7 days; featured "Izdvojeno", 8 h or 24 h) moves
    `date` and may change the featured fields, all at once; none of it is a change."""
    row = recorded["summary_row"]
    renewed = {
        **row,
        "date": row["date"] + 24 * 3600,
        "sponsored": 2,
        "pinned": True,
        "premium_badges": ["x"],
        "labels": ["x"],
        "score": 9.5,
        "refresh_available": True,
    }
    assert summary_hash(renewed) == summary_hash(row)
    assert needs_detail_fetch(renewed, summary_hash(row)) is False


def test_needs_detail_fetch_only_for_new_or_changed_summaries(recorded: dict[str, Any]) -> None:
    row = recorded["summary_row"]
    assert needs_detail_fetch(row, None) is True
    assert needs_detail_fetch(row, summary_hash(row)) is False
    assert needs_detail_fetch({**row, "date": 2}, summary_hash(row)) is False
    assert needs_detail_fetch({**row, "price": 1}, summary_hash(row)) is True


# --- ListingDetail and detail hash --------------------------------------------------------


def test_listing_detail_from_api(recorded: dict[str, Any]) -> None:
    body = detail_body(recorded, 63996799)
    detail = ListingDetail.from_api(body)
    assert detail.id == 63996799
    assert detail.slug.endswith("-63996799")
    assert detail.url == f"https://martom.olx.ba/artikal/{detail.slug}"
    assert detail.title == body["title"]
    assert detail.description_html == body["additional"]["description"]
    assert detail.description_text == clean_description(body["additional"]["description"])
    assert detail.price == body["price"]
    assert detail.category_id == 881
    assert ("Materijal ograde", "Panelna") in detail.attributes


def test_listing_detail_rejects_a_body_without_id_or_title() -> None:
    from martom.olx.sync import OlxResponseError

    with pytest.raises(OlxResponseError):
        ListingDetail.from_api({"title": "x"})
    with pytest.raises(OlxResponseError):
        ListingDetail.from_api({"id": 1})
    with pytest.raises(OlxResponseError):
        ListingDetail.from_api(["not", "a", "dict"])  # type: ignore[arg-type]


def test_listing_detail_handles_missing_description_and_attributes() -> None:
    detail = ListingDetail.from_api({"id": 5, "title": "T", "slug": "t-5", "additional": None})
    assert detail.description_html == ""
    assert detail.description_text == ""
    assert detail.attributes == ()


def test_listing_detail_skips_attributes_without_a_value() -> None:
    body = {
        "id": 5,
        "title": "T",
        "slug": "t-5",
        "attributes": [{"name": "Boja", "value": None}, {"name": "Tip", "value": "Panel"}],
    }
    assert ListingDetail.from_api(body).attributes == (("Tip", "Panel"),)


@pytest.mark.parametrize(
    "bad",
    [
        {"slug": ""},
        {"slug": None},
        {"price": "45 KM"},
        {"price": True},
        {"display_price": 45},
        {"state": 1},
        {"status": ["active"]},
        {"available": "yes"},
        {"short_description": 3},
    ],
)
def test_listing_detail_rejects_fields_of_the_wrong_type(bad: dict[str, Any]) -> None:
    from martom.olx.sync import OlxResponseError

    body = {"id": 5, "title": "T", "slug": "t-5", "price": 45.0, **bad}
    with pytest.raises(OlxResponseError):
        ListingDetail.from_api(body)


def test_listing_detail_accepts_an_integer_price() -> None:
    assert ListingDetail.from_api({"id": 5, "title": "T", "slug": "t-5", "price": 45}).price == 45


def test_detail_hash_ignores_footer_edits(recorded: dict[str, Any]) -> None:
    """Toni changing the footer on every listing must not trigger 2,600 re-extractions."""
    body = detail_body(recorded, 36376333)
    edited = copy.deepcopy(body)
    edited["additional"]["description"] = body["additional"]["description"].replace(
        "info@martom-bekaert.com", "prodaja@martom-bekaert.com"
    )
    assert edited != body
    assert ListingDetail.from_api(edited).detail_hash == ListingDetail.from_api(body).detail_hash


def test_detail_hash_ignores_views_and_date(recorded: dict[str, Any]) -> None:
    body = detail_body(recorded, 36376333)
    noisy = {**body, "views": 123456, "date": 1, "quantity": 3}
    assert ListingDetail.from_api(noisy).detail_hash == ListingDetail.from_api(body).detail_hash


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b["additional"].update(
            description=b["additional"]["description"].replace("18,00KM", "19,00KM")
        ),
        lambda b: b.update(title="KATANAC KRATKI"),
        lambda b: b.update(price=99),
        lambda b: b.update(attributes=[{"name": "Boja", "value": "Crna"}]),
        lambda b: b.update(state="used"),
    ],
)
def test_detail_hash_changes_when_facts_change(recorded: dict[str, Any], change: Any) -> None:
    body = detail_body(recorded, 36376333)
    changed = copy.deepcopy(body)
    change(changed)
    assert ListingDetail.from_api(changed).detail_hash != ListingDetail.from_api(body).detail_hash


# --- Fetching -----------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


def make_client(recorded: dict[str, Any], requested: list[int], **responses: Any) -> OlxClient:
    """A client whose fake OLX serves the recorded details; records each id requested.

    `responses` maps "id_<n>" to an `httpx.Response` to send instead (errors, 404s).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        listing_id = int(request.url.path.rsplit("/", 1)[1])
        requested.append(listing_id)
        override = responses.get(f"id_{listing_id}")
        if override is not None:
            return override
        return httpx.Response(200, json=recorded["details"][str(listing_id)])

    clock = FakeClock()
    return OlxClient(
        transport=httpx.MockTransport(handler),
        clock=clock.monotonic,
        sleep=clock.sleep,
        max_attempts=1,
    )


def summary_rows(recorded: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Summary rows for every recorded listing (the real row, re-keyed per id)."""
    base = recorded["summary_row"]
    return {
        int(i): {**base, "id": int(i), "title": d["title"], "slug": d["slug"]}
        for i, d in recorded["details"].items()
    }


@pytest.mark.anyio
async def test_unchanged_listings_are_not_refetched(recorded: dict[str, Any]) -> None:
    rows = summary_rows(recorded)
    stored = {listing_id: summary_hash(row) for listing_id, row in rows.items()}
    changed_id, new_id = 36376333, 54710841
    rows[changed_id] = {**rows[changed_id], "price": 7.5}
    del stored[new_id]
    # A bumped date alone is not a change.
    rows[1199211] = {**rows[1199211], "date": 1}

    requested: list[int] = []
    async with make_client(recorded, requested) as olx:
        result = await fetch_changed_details(olx, rows, stored)

    assert sorted(requested) == sorted([changed_id, new_id])
    assert set(result.fetched) == {changed_id, new_id}
    assert set(result.unchanged) == set(rows) - {changed_id, new_id}
    assert result.failed == {}
    fetched = result.fetched[changed_id]
    assert fetched.summary_hash == summary_hash(rows[changed_id])
    assert fetched.detail.id == changed_id
    assert fetched.detail.description_text == cleaned(recorded, changed_id)


@pytest.mark.anyio
async def test_a_failed_listing_is_reported_and_the_rest_continue(
    recorded: dict[str, Any],
) -> None:
    rows = summary_rows(recorded)
    requested: list[int] = []
    gone = httpx.Response(404, json={"message": "not found"})
    async with make_client(recorded, requested, id_36376333=gone) as olx:
        result = await fetch_changed_details(olx, rows, {})

    assert set(requested) == set(rows)
    assert set(result.failed) == {36376333}
    assert isinstance(result.failed[36376333], OlxApiError)
    assert result.failed[36376333].status_code == 404
    assert set(result.fetched) == set(rows) - {36376333}


@pytest.mark.anyio
async def test_a_malformed_detail_is_reported_as_failed(recorded: dict[str, Any]) -> None:
    rows = summary_rows(recorded)
    requested: list[int] = []
    broken = httpx.Response(200, json={"error": "nothing here"})
    async with make_client(recorded, requested, id_24449780=broken) as olx:
        result = await fetch_changed_details(olx, rows, {})
    assert set(result.failed) == {24449780}


@pytest.mark.anyio
async def test_a_detail_for_another_id_is_reported_as_failed(recorded: dict[str, Any]) -> None:
    rows = summary_rows(recorded)
    requested: list[int] = []
    other = httpx.Response(200, json=recorded["details"]["1199211"])
    async with make_client(recorded, requested, id_24449780=other) as olx:
        result = await fetch_changed_details(olx, rows, {})
    assert set(result.failed) == {24449780}


@pytest.mark.anyio
async def test_rate_limit_abort_stops_the_whole_fetch(recorded: dict[str, Any]) -> None:
    rows = summary_rows(recorded)
    requested: list[int] = []
    first = next(iter(rows))
    abort = httpx.Response(429, headers={"Retry-After": "999999"})
    async with make_client(recorded, requested, **{f"id_{first}": abort}) as olx:
        with pytest.raises(OlxRateLimitAbortError):
            await fetch_changed_details(olx, rows, {})
    assert requested == [first]


@pytest.mark.anyio
async def test_details_fetched_before_an_abort_reach_the_caller(recorded: dict[str, Any]) -> None:
    """A run that hits the rate-limit wall late must not lose what it already fetched."""
    rows = summary_rows(recorded)
    ids = list(rows)
    third = ids[2]
    stored = {ids[-1]: summary_hash(rows[ids[-1]])}  # one unchanged listing
    requested: list[int] = []
    abort = httpx.Response(429, headers={"Retry-After": "999999"})
    async with make_client(recorded, requested, **{f"id_{third}": abort}) as olx:
        with pytest.raises(DetailFetchAbortedError) as caught:
            await fetch_changed_details(olx, rows, stored)

    assert isinstance(caught.value, OlxRateLimitAbortError)  # old `except` clauses still work
    assert isinstance(caught.value.__cause__, OlxRateLimitAbortError)
    partial = caught.value.partial
    assert set(partial.fetched) == set(ids[:2])
    assert partial.fetched[ids[0]].detail.id == ids[0]
    assert partial.failed == {}
    assert requested == ids[:3]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
