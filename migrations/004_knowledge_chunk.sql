-- 004: `knowledge_chunk`, Toni's fence know-how (one row per Markdown file in knowledge/).
-- Filled by `martom load-knowledge` (M2-4), keyed by the chunk's front matter `id`; the
-- files are the source of truth, so the loader also deletes rows whose file is gone.
-- Embeddings for these rows arrive in M3-3, in their own migration.
-- Every statement re-runs safely (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS knowledge_chunk (
    id           text PRIMARY KEY,                -- front matter `id`, e.g. 'panelna-ili-rola'
    title        text NOT NULL,
    topic        text NOT NULL,                   -- e.g. 'fence-systems', 'posts', 'anchors'
    sources      text[] NOT NULL,                 -- where each rule came from (fence-rules.md, Toni's answers)
    -- 'draft' until Toni has read and approved the chunk; the loader stores what the file says.
    status       text NOT NULL CHECK (status IN ('draft', 'approved')),
    body         text NOT NULL,                   -- the Markdown after the front matter
    source_path  text NOT NULL,                   -- file name in knowledge/
    content_hash text NOT NULL,                   -- SHA-256 of the whole file: skip unchanged rows
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
