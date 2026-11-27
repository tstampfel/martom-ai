"""The extracted variants of the listings the BOM price map points at, for the tests.

`VARIANTS`: the six mapped listings as the local synced catalogue had them on 27 Nov 2026
(tests/fixtures/bom_pricing/variants.json). `GOLDEN_VARIANTS`: the two of them that are in
the golden extraction fixture, extracted by another run that wrote some fields differently
("H-125 cm" for "H-125", a price inside a label), so the map is tested against both.
Imported as a plain module (pytest's `pythonpath` includes tests/).
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from martom.extract.store import VariantRow

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PANEL_LISTING = 63996799
T_POST_LISTING = 59473597
FOOT_PLATE_LISTING = 81645615
TENSIONER_LISTING = 18129143
WIRE_LISTING = 46677177
WIRE_HOLDER_LISTING = 48475613


def _variant(row: dict[str, Any]) -> VariantRow:
    fields = {name: row[name] for name in VariantRow.__dataclass_fields__}
    fields["price"] = Decimal(row["price"]) if row["price"] is not None else None
    return VariantRow(**fields)


def _load(path: Path, key: str) -> dict[int, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))[key]
    return {int(listing_id): rows for listing_id, rows in data.items()}


VARIANTS: dict[int, list[VariantRow]] = {
    listing_id: [_variant(row) for row in rows]
    for listing_id, rows in _load(FIXTURES / "bom_pricing" / "variants.json", "listings").items()
}

GOLDEN_VARIANTS: dict[int, list[VariantRow]] = {
    listing_id: [_variant(row) for row in record["variants"]]
    for listing_id, record in _load(FIXTURES / "extraction" / "golden.json", "records").items()
    if listing_id in VARIANTS
}
