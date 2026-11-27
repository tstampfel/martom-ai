-- 007: embeddings for hybrid search (M3-3). Every statement re-runs safely.
--
-- product and knowledge_chunk each get:
--   embedding        vector(1024): the text's embedding (pgvector, extension from 001).
--                    1024 because every allowed model returns 1024 numbers, so a
--                    model change never needs a migration (src/martom/embeddings.py).
--   embedding_model  the model that made it; search only compares vectors of one model.
--   embedding_hash   sha256 of model + embedded text (`text_hash` in embeddings.py):
--                    `martom embed` skips rows whose hash is unchanged.
-- olx_listing is not touched, so the active_olx_listing view needs no re-creating.
--
-- Needs pgvector 0.8 or later: vector search sets hnsw.iterative_scan (search/vector.py),
-- which older versions reject ("hnsw" is a reserved prefix), so every vector search would
-- fail. Checked here, so an older server (e.g. an RDS engine version with pgvector < 0.8)
-- fails at migrate with a clear message instead. Compare as integer arrays: as text,
-- '0.10' < '0.8'.
DO $$
DECLARE
    installed text := (SELECT extversion FROM pg_extension WHERE extname = 'vector');
BEGIN
    IF string_to_array(split_part(installed, '-', 1), '.')::int[] < ARRAY[0, 8] THEN
        RAISE EXCEPTION 'pgvector % is installed; 0.8 or later is needed (ALTER EXTENSION vector UPDATE, or a newer server)', installed;
    END IF;
END
$$;

ALTER TABLE product ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE product ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE product ADD COLUMN IF NOT EXISTS embedding_hash text;

ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS embedding_hash text;

-- HNSW: an approximate nearest-neighbour index for "ORDER BY embedding <=> query"
-- (<=> is cosine distance, so the index uses the cosine operator class). On ~2,600 rows
-- Postgres may still prefer reading every row; the index matters as the catalogue grows.
-- knowledge_chunk has a few dozen rows and needs no index.
CREATE INDEX IF NOT EXISTS product_embedding_hnsw_idx
    ON product USING hnsw (embedding vector_cosine_ops);
