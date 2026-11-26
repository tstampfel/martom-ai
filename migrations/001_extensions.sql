-- 001: extensions and the `martom` full-text search configuration.
-- Every statement re-runs safely (IF NOT EXISTS / existence checks).

CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector: embeddings (M3)
CREATE EXTENSION IF NOT EXISTS unaccent;  -- folds č ć š ž đ to c c s z d
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram similarity for typos and model codes

-- `martom` text search configuration: unaccent, then the Serbian Snowball stemmer.
--
-- Decision (M0-2): Postgres 16 ships a Snowball stemmer for Serbian Latin, the closest to
-- Bosnian/Croatian. Tested on catalogue words, it joins most inflected forms, also when
-- the input has no diacritics:
--   ograda / ograde / ogradu -> ograd     žica / žice / zice -> zic
--   pocinčana / pocincani    -> pocincan  brusilica / brusilice -> brusilic
-- Known misses: panel -> pane but paneli -> panel; stup -> stup but stupovi -> stupov.
-- Those are covered by synonyms in product.search_text (M2) and by the vector half of
-- hybrid search (M3). The plain `simple` dictionary would join no inflected forms at all.
-- The M3 retrieval eval can revisit this choice; changing it means a new migration.
--
-- unaccent runs first and is a filtering dictionary, so the stemmer always sees text
-- without diacritics and "pocincana" (typed on a phone) matches "pocinčana".
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'martom') THEN
        CREATE TEXT SEARCH CONFIGURATION martom (COPY = pg_catalog.serbian);
        ALTER TEXT SEARCH CONFIGURATION martom
            ALTER MAPPING FOR asciiword, asciihword, hword_asciipart,
                              word, hword, hword_part
            WITH unaccent, serbian_stem;
    END IF;
END
$$;
