"""Write Toni's review sheet for the BOM price map (#125) as Markdown under data/.

Reads src/martom/bom/price_map.toml, manual_products.toml and the database in DATABASE_URL
(default: the local compose database), read-only: active listings and their extracted
variants. No model, no network beyond the database. The sheet holds catalogue data, so it
is written only under data/ (git-ignored); turn it into a PDF from there.

    uv run python scripts/price_map_review_sheet.py
    uv run python scripts/price_map_review_sheet.py --out data/cijene-ograda.md
"""

import argparse
from datetime import date
from pathlib import Path

import psycopg

from martom.bom.manual_products import load_manual_products
from martom.bom.pricing import load_price_map
from martom.review.price_map_sheet import (
    build_rows,
    entry_listing_ids,
    load_listings,
    load_roll_candidates,
    render_markdown,
)
from martom.settings import Settings

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=DATA_DIR / f"price-map-review-{today:%Y-%m-%d}.md",
        help="output file; must be under data/",
    )
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(DATA_DIR) or out == DATA_DIR or out.is_dir():
        parser.error(f"--out must be a file under {DATA_DIR} (git-ignored), got {out}")

    price_map = load_price_map()
    manual_products = load_manual_products()
    with psycopg.connect(Settings().database_url) as conn:
        conn.read_only = True  # every transaction of this session is read-only
        listings = load_listings(conn, entry_listing_ids(price_map))
        roll_candidates = load_roll_candidates(conn)
    rows = build_rows(price_map, listings, manual_products, roll_candidates=roll_candidates)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(rows, today=today, status=price_map.status), encoding="utf-8")
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
