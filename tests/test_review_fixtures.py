"""The review checks on real listings (tests/fixtures/extraction/) and on the audited copy.

The extracted products come from responses.json (replies recorded from claude-haiku-5-5)
turned into rows by the same `build_record` the extraction
job uses, so these tests read exactly what `product_variant` would hold. No model and no
network are called.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from martom.extract.job import build_record
from martom.extract.prompt import ListingInput
from martom.extract.schema import ExtractedProduct
from martom.olx.detail import ListingDetail
from martom.review.checks import CheckListing, CheckProduct, check_listing

FIXTURES = Path(__file__).parent / "fixtures"
LISTINGS = json.loads((FIXTURES / "extraction" / "listings.json").read_text(encoding="utf-8"))[
    "listings"
]
RESPONSES = json.loads((FIXTURES / "extraction" / "responses.json").read_text(encoding="utf-8"))[
    "responses"
]
AUDITED = json.loads((FIXTURES / "review" / "63996799_audited.json").read_text(encoding="utf-8"))

SIZE_AND_PRICE_KINDS = {
    "implausible_dimension",
    "height_without_price",
    "variant_without_price",
    "unreadable_price",
    "price_conflict",
    "listing_price_mismatch",
}


def check_listing_for(listing_id: str, reply: dict[str, Any]) -> CheckListing:
    """A CheckListing as the job would load it: listing fields plus the extracted rows."""
    body = LISTINGS[listing_id]
    detail = ListingDetail.from_api(body)
    record = build_record(
        ListingInput.from_detail(detail), ExtractedProduct.model_validate(reply), model="fixture"
    )
    return CheckListing(
        id=detail.id,
        slug=body["slug"],
        title=detail.title,
        description_text=detail.description_text,
        # OLX sends a float; the database column is numeric, so the job reads a Decimal.
        price=None if detail.price is None else Decimal(str(detail.price)),
        display_price=detail.display_price,
        price_by_agreement=detail.price_by_agreement,
        state=detail.state,
        attributes=tuple(
            (str(a["name"]), str(a["value"])) for a in body["attributes"] if a.get("value")
        ),
        # The trimmed fixtures keep no image list (photos come from the listing-page row).
        photo_count=1,
        product=CheckProduct(record.product_type, record.material, record.variants),
    )


def kinds_for(listing_id: str, reply: dict[str, Any]) -> set[str]:
    return {issue.kind for issue in check_listing(check_listing_for(listing_id, reply))}


def test_audited_panel_fence_63996799_raises_the_audit_findings() -> None:
    """docs/PLAN.md M2-2 done-when, on the audited copy (see its _about)."""
    issues = check_listing(check_listing_for("63996799", AUDITED["product"]))
    by_kind = {issue.kind: issue for issue in issues}
    assert {"implausible_dimension", "height_without_price"} <= set(by_kind)
    assert by_kind["implausible_dimension"].details["value"] == "2500CM"
    assert by_kind["height_without_price"].details["height"] == "173"


def test_real_panel_fence_63996799_has_no_size_or_price_issues() -> None:
    """The listing as recorded on 26 Nov 2026 is clean: no false positives."""
    assert kinds_for("63996799", RESPONSES["63996799"]) & SIZE_AND_PRICE_KINDS == set()


# What the checks find on each real listing; every finding was read and is a real problem.
EXPECTED_REAL = {
    "63996799": set(),
    "1199211": {"price_on_request", "typo"},  # "Na upit", no price; "pluči"
    "24449780": {"wrong_material"},  # FARMER PLETIVO marked Materijal ograde = Kovana
    "36376333": set(),
    "54710841": {"markup"},  # "$\\varnothing 1,80\\text{ mm}$" left in the description
    "35990143": set(),
    "59473597": set(),
    "68378185": set(),
    "52889522": set(),
    "31905913": set(),
}


def test_real_listings_raise_exactly_the_known_problems() -> None:
    assert set(EXPECTED_REAL) == set(RESPONSES)
    found = {listing_id: kinds_for(listing_id, reply) for listing_id, reply in RESPONSES.items()}
    assert found == EXPECTED_REAL
