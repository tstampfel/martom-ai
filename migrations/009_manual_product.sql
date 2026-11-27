-- 009: manual products in search (#112).
--
-- Products Toni sells without an OLX listing live in src/martom/bom/manual_products.toml.
-- This table is a search index of that file, nothing more: `martom load-manual-products`
-- (and `martom embed`, before it embeds) upserts one row per TOML entry, keyed by the
-- entry's table name, and deletes rows whose entry is gone. The TOML stays the source of
-- every value; never edit these rows by hand.
--
-- The text columns mirror `product` so keyword and vector search treat both alike:
-- search_tsv and search_norm go through martom_search_normalize (migration 006), the
-- embedding columns follow migration 007. price_km is a copy of the TOML price, kept only
-- so the search price filter can apply; anything that shows a price reads the TOML.
--
-- No indexes beyond the primary key: the table holds a handful of rows, and a sequential
-- scan of it is cheaper than any index lookup.

CREATE TABLE IF NOT EXISTS manual_product (
    key             text PRIMARY KEY,           -- the TOML table name, e.g. "anchor_m8x70"
    name            text NOT NULL,              -- as written in the TOML, e.g. "Anker M8×70"
    product_type    text NOT NULL,
    -- Copy of the TOML price, for the filter only. Plain numeric: the TOML loader accepts
    -- any precision, and a rounded copy could disagree with the price shown (R1-4).
    price_km        numeric NOT NULL,
    search_text     text NOT NULL,
    search_tsv      tsvector
        GENERATED ALWAYS AS (to_tsvector('martom', martom_search_normalize(search_text))) STORED,
    search_norm     text GENERATED ALWAYS AS (martom_search_normalize(search_text)) STORED,
    content_hash    text NOT NULL,              -- SHA-256 of the entry's fields
    embedding       vector(1024),
    embedding_model text,
    embedding_hash  text,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);
