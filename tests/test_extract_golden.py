"""Golden tests: 10 real listings through the extractor with recorded model replies.

Inputs: tests/fixtures/extraction/listings.json (real OLX listings: fences, posts, wire,
mesh and other products). Model replies: responses.json, served by a fake model, so no
API call is made. Expected rows: golden.json, the `product` / `product_variant` rows the
pipeline builds from them (prices parsed, search_text built).

When the prompt or schema changes, re-record the replies and rewrite golden.json with
`uv run python scripts/record_extraction_fixtures.py`, then review the diff: it shows
exactly how the extraction changed (docs/PLAN.md, "a change in extraction prompt shows the
diff in variants").
"""

import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.extract.extractor import Extractor
from martom.extract.job import build_record
from martom.extract.prompt import ListingInput, listing_message
from martom.extract.schema import ExtractedProduct
from martom.extract.store import ProductRecord
from martom.olx.detail import ListingDetail

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


LISTINGS = {
    int(listing_id): ListingInput.from_detail(ListingDetail.from_api(body))
    for listing_id, body in load("listings.json")["listings"].items()
}
RESPONSES = load("responses.json")["responses"]


def recorded_model(messages: Sequence[BaseMessage]) -> ExtractedProduct:
    """Plays back the recorded reply for the listing named in the prompt."""
    match = re.search(r'<listing id="(\d+)">', messages[1].text)
    assert match is not None
    return ExtractedProduct.model_validate(RESPONSES[match.group(1)])


def as_json(record: ProductRecord) -> dict[str, Any]:
    """The record as golden.json stores it (Decimal prices as strings)."""
    return json.loads(json.dumps(asdict(record), default=str, ensure_ascii=False))


@pytest.fixture
async def records() -> dict[int, ProductRecord]:
    extractor = Extractor(RunnableLambda(recorded_model), model_name="recorded")
    results = await extractor.extract_many(list(LISTINGS.values()))
    assert all(r.error is None for r in results), [r.error for r in results]
    return {
        r.listing_id: build_record(LISTINGS[r.listing_id], r.product, model="recorded")
        for r in results
        if r.product is not None
    }


def test_the_golden_set_has_10_real_listings_fences_and_other_products() -> None:
    assert len(LISTINGS) == 10
    # The product_type values exactly as the recorded Haiku replies write them.
    types = {RESPONSES[str(i)]["product_type"] for i in LISTINGS}
    assert {"panelna ograda", "t stup za ogradu", "farmer pletivo"} <= types  # fences
    assert {"kutna brusilica", "tiplo", "katanac"} <= types  # other products


async def test_records_match_golden(records: dict[int, ProductRecord]) -> None:
    golden = load("golden.json")["records"]

    assert {str(i): as_json(r) for i, r in records.items()} == golden


async def test_variant_values_are_copied_verbatim_from_the_listing(
    records: dict[int, ProductRecord],
) -> None:
    # Extraction copies, never fixes: every value must appear, as written, in what the
    # model was shown (title, OLX fields such as "cijena na OLX-u: 57,50 KM", description).
    for listing_id, record in records.items():
        source = listing_message(LISTINGS[listing_id])
        for variant in record.variants:
            for value in (variant.label, variant.height, variant.length, variant.colour,
                          variant.size, variant.price_text):  # fmt: skip
                assert value is None or value in source, (listing_id, value)


async def test_key_facts(records: dict[int, ProductRecord]) -> None:
    panel = records[63996799]
    by_label = {v.label.split(":")[0]: v for v in panel.variants}
    v153 = by_label["3D Panelna ograda 153 x 250 cm (Ø 4,20/4,40 mm PVC)"]
    assert (v153.height, v153.length, v153.price_text, str(v153.price)) == (
        "153",
        "250 cm",
        "53,00 KM",
        "53.00",
    )
    assert len(panel.variants) == 17  # 10 panels, 5 posts, clip, anchor

    # Nylofor lists sizes without any price ("Na upit"): every price stays empty.
    assert all(v.price is None for v in records[1199211].variants)
    # One grinder: one variant, size and price copied from the description
    # ("Prečnik diska: 125 mm", "CIJENA: ➡️ 89,00 KM").
    [grinder] = records[52889522].variants
    assert (grinder.size, grinder.price_text, str(grinder.price)) == ("125 mm", "89,00 KM", "89.00")
    # Typos are kept: "FISHER" stays in the label.
    assert records[31905913].variants[3].label == "TIPLO FISHER FIŠER SX12"
    assert records[31905913].variants[3].price_text == "0,75KM"
    assert str(records[31905913].variants[0].price) == "0.15"
    # search_text: facts plus the stup/stub synonym, no marketing text.
    assert "stub" in records[59473597].search_text
    assert "Masivan" not in records[59473597].search_text
