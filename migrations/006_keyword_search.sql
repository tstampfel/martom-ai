-- 006: keyword search (M3-2). Every statement re-runs safely.
--
-- 1. martom_search_normalize(text): one normaliser for both sides of full-text and trigram
--    search, so documents and queries are cut into words the same way.
-- 2. product.search_tsv is re-created on the normalised search_text, with its GIN index.
-- 3. martom_height_cm(text): reads a height as written ("830 mm", "H-100CM", "1,5 m") as
--    centimetres, for the height filter. The stored text is never changed (CLAUDE.md:
--    extraction copies, never fixes); this only reads it.

-- Why a normaliser before to_tsvector (the M0-2 review note on #14): the `martom` config
-- unaccents each token, but the parser cuts the text into tokens first. "žica/pocinčana"
-- is one "file path" token there, so "zica pocincana" never matched it; even with
-- unaccent first it stays one token ('zica/pocincana'). So, before the parser sees it:
--   - unaccent and lower case,
--   - '/' and '\' become spaces              ("žica/pocinčana" -> "zica pocincana"),
--   - a hyphen between a letter and a digit becomes a space ("H-100" -> "h 100"),
--   - a number glued to its unit is split    ("153CM" -> "153 cm", "830mm" -> "830 mm").
-- unaccent() itself is only STABLE (its dictionary could change), and a generated column
-- or index needs an IMMUTABLE function, so this wrapper names the dictionary explicitly
-- and is declared IMMUTABLE: the usual, documented workaround. The unaccent extension
-- lives in `public` (migration 001).
CREATE OR REPLACE FUNCTION martom_search_normalize(input text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
AS $$
    SELECT regexp_replace(
        regexp_replace(
            regexp_replace(
                lower(public.unaccent('public.unaccent'::regdictionary, input)),
                '[/\\]', ' ', 'g'),
            '([a-z])-([0-9])', '\1 \2', 'g'),
        '([0-9])(mm|cm|metara|metra|met|m|kom|kg|w)\M', '\1 \2', 'g')
$$;

-- Re-create search_tsv on the normalised text, unless an earlier run already did. A
-- generated column's expression can't be altered, only dropped and added again (which
-- recomputes it for every row and drops its index with it).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute AS a
        JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'product'::regclass
          AND a.attname = 'search_tsv'
          AND pg_get_expr(d.adbin, d.adrelid) LIKE '%martom_search_normalize%'
    ) THEN
        ALTER TABLE product DROP COLUMN IF EXISTS search_tsv;
        ALTER TABLE product ADD COLUMN search_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('martom', martom_search_normalize(search_text)))
            STORED;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS product_search_tsv_idx ON product USING gin (search_tsv);

-- No trigram index: keyword search scores trigram similarity per query word in a LATERAL
-- subquery, which no index serves; at ~2,600 products a sequential scan is fast enough.

-- A height as written -> centimetres, or NULL when there is no number. The first number
-- counts ("153 X 2500CM" -> 153). Its unit: "mm" divides by 10; "m", "met", "metra" or
-- "metara" (as a word of their own, so "1,5 metara" is 150) multiply by 100; "cm" is cm.
-- No unit: below 10 it can only be metres ("1,5" -> 150), otherwise cm ("153").
CREATE OR REPLACE FUNCTION martom_height_cm(raw text)
RETURNS numeric
LANGUAGE plpgsql
IMMUTABLE PARALLEL SAFE STRICT
AS $$
DECLARE
    parts text[];
    amount numeric;
BEGIN
    parts := regexp_match(
        lower(raw), '([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|metara|metra|met|m)?\M');
    IF parts IS NULL THEN
        RETURN NULL;
    END IF;
    amount := replace(parts[1], ',', '.')::numeric;
    RETURN CASE
        WHEN parts[2] = 'mm' THEN amount / 10
        WHEN parts[2] IN ('m', 'met', 'metra', 'metara') THEN amount * 100
        WHEN parts[2] IS NULL AND amount < 10 THEN amount * 100
        ELSE amount
    END;
END
$$;
