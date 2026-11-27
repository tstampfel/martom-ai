-- 003: `product` (one structured extraction per OLX listing) and `product_variant` (one
-- row per height x length x colour / size, as the listing writes it). Filled by
-- `martom extract` (M2-1). Every statement re-runs safely (IF NOT EXISTS / existence checks).
--
-- Extraction copies, never fixes: every text column holds what the listing says, exactly
-- as written ("153 X 2500CM" stays that way). The deterministic checks (M2-2) flag
-- problems into review_issue for Toni.

CREATE TABLE IF NOT EXISTS product (
    listing_id      bigint PRIMARY KEY REFERENCES olx_listing (id),
    -- olx_listing.detail_hash this row was extracted from. `martom extract` re-extracts a
    -- listing only when the two differ (or it has no product row yet).
    detail_hash     text NOT NULL,
    product_type    text NOT NULL,              -- in Bosnian, e.g. "panelna ograda"
    brand           text,
    material        text,
    wire_thickness  text,                       -- as written, e.g. "Ø 4,20/4,40 mm"
    mesh            text,                       -- mesh opening as written, e.g. "25MM"
    colours         text[] NOT NULL DEFAULT '{}',
    uses            text[] NOT NULL DEFAULT '{}',
    compatible_with text[] NOT NULL DEFAULT '{}',
    synonyms        text[] NOT NULL DEFAULT '{}',
    -- The whole validated model output, so a later issue can read fields without a new
    -- extraction run.
    extraction      jsonb NOT NULL,
    model           text NOT NULL,              -- e.g. claude-haiku-5-5
    prompt_version  text NOT NULL,
    -- Compact text that search indexes (title, type, brand, colours, heights, mesh, uses,
    -- compatibility, synonyms); the marketing description is deliberately left out.
    search_text     text NOT NULL,
    search_tsv      tsvector GENERATED ALWAYS AS (to_tsvector('martom', search_text)) STORED,
    extracted_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS product_search_tsv_idx ON product USING gin (search_tsv);

CREATE TABLE IF NOT EXISTS product_variant (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id  bigint NOT NULL REFERENCES product (listing_id) ON DELETE CASCADE,
    position    integer NOT NULL,               -- order in the listing, from 0
    label       text NOT NULL,                  -- the variant's line or phrase, verbatim
    height      text,                           -- as written, e.g. "153", "H-100CM"
    length      text,                           -- as written, e.g. "250 cm", "20METARA"
    colour      text,
    size        text,                           -- any other size: "SX8", "125 mm", "50MM"
    price_text  text,                           -- price as written, e.g. "44,00 KM"
    price       numeric(12, 2),                 -- price_text parsed in Python; NULL if it can't be
    unit        text,                           -- as written, e.g. "panel", "kom", "ROLA 50M"
    CONSTRAINT product_variant_position_key UNIQUE (listing_id, position)
);

-- sync_run also records extraction runs (docs/PLAN.md: "each sync/extraction run"). An
-- extraction run uses `seen` (listings that needed extraction), `extracted` (saved),
-- `errors` and `failed_listing_ids` (listings whose extraction still failed after its
-- retries; they keep their old product row, or none, and are tried again next run).
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS extracted integer NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sync_run_kind_check'
          AND pg_get_constraintdef(oid) LIKE '%extraction%'
    ) THEN
        ALTER TABLE sync_run DROP CONSTRAINT IF EXISTS sync_run_kind_check;
        ALTER TABLE sync_run ADD CONSTRAINT sync_run_kind_check
            CHECK (kind IN ('nightly', 'full', 'apply_skipped', 'extraction'));
    END IF;
END
$$;
