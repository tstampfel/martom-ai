"""Re-record the golden extraction fixtures (M2-1). Run by hand, never in CI.

    # rewrite golden.json from the replies already in responses.json (no API call):
    uv run python scripts/record_extraction_fixtures.py

    # call claude-haiku-5-5 for the 10 golden listings, save its replies to
    # responses.json, then rewrite golden.json (costs a few cents; needs the key in your
    # shell environment, e.g. `ANTHROPIC_API_KEY=... uv run python ...`):
    uv run python scripts/record_extraction_fixtures.py --record

Review the git diff of tests/fixtures/extraction/ afterwards: it shows exactly what the
prompt or schema change did to the extracted variants.
"""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from martom.extract.extractor import Extractor, make_structured_model
from martom.extract.job import build_record
from martom.extract.prompt import PROMPT_VERSION, ListingInput
from martom.extract.schema import ExtractedProduct
from martom.olx.detail import ListingDetail
from martom.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "extraction"


def _read(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _write(name: str, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    (FIXTURES / name).write_text(text + "\n", encoding="utf-8")


async def _record(listings: dict[int, ListingInput]) -> None:
    settings = Settings()
    extractor = Extractor(make_structured_model(settings), model_name=settings.fast_model)
    results = await extractor.extract_many(list(listings.values()))
    failed = [r for r in results if r.product is None]
    if failed:
        raise SystemExit(f"extraction failed for: {[(r.listing_id, r.error) for r in failed]}")
    _write(
        "responses.json",
        {
            "_about": (
                "Model replies for the 10 golden listings (listings.json), keyed by listing "
                f"id, recorded from {settings.fast_model} on {date.today().isoformat()} with "
                "scripts/record_extraction_fixtures.py --record."
            ),
            "source": "recorded",
            "model": settings.fast_model,
            "prompt_version": PROMPT_VERSION,
            "responses": {
                str(r.listing_id): r.product.model_dump(mode="json")
                for r in results
                if r.product is not None
            },
        },
    )


def _write_golden(listings: dict[int, ListingInput]) -> None:
    responses = _read("responses.json")["responses"]
    records = {}
    for listing_id, listing in listings.items():
        product = ExtractedProduct.model_validate(responses[str(listing_id)])
        records[str(listing_id)] = asdict(build_record(listing, product, model="recorded"))
    _write(
        "golden.json",
        {
            "_about": (
                "The product / product_variant rows the pipeline builds from responses.json "
                "(prices parsed, search_text built). Written by "
                "scripts/record_extraction_fixtures.py; checked by tests/test_extract_golden.py."
            ),
            "records": records,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="call Claude Haiku first")
    args = parser.parse_args()
    listings = {
        int(listing_id): ListingInput.from_detail(ListingDetail.from_api(body))
        for listing_id, body in _read("listings.json")["listings"].items()
    }
    if args.record:
        asyncio.run(_record(listings))
    _write_golden(listings)
    print(f"wrote {FIXTURES / 'golden.json'}")


if __name__ == "__main__":
    main()
