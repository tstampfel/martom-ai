-- 005: `review_issue`, Toni's review queue (M2-2). The deterministic checks in
-- src/martom/review/ write one row per problem they find in a listing (or in
-- manual_products.toml); nothing here changes the listing or the extracted product.
-- Every statement re-runs safely (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS review_issue (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- sha256 of (kind, listing id, what makes the issue distinct: the variant's sizes and
    -- colour, the misspelt word, the shared title of duplicates; never model-written text).
    -- The checks upsert on it, so rerunning them never adds a second row for one problem.
    fingerprint   text NOT NULL CONSTRAINT review_issue_fingerprint_key UNIQUE,
    kind          text NOT NULL,                -- the check, e.g. 'implausible_dimension'
    -- The listing the problem is in; NULL for a problem that isn't about one listing
    -- (a stale manual_products.toml entry). For duplicates: the lowest of the listing ids.
    listing_id    bigint REFERENCES olx_listing (id),
    priority      text NOT NULL CONSTRAINT review_issue_priority_check
                      CHECK (priority IN ('high', 'medium', 'low')),
    message       text NOT NULL,                -- for Toni, in Bosnian
    details       jsonb NOT NULL DEFAULT '{}',  -- the values the check looked at
    --   open      the latest check run still finds the problem
    --   resolved  a later run no longer found it; details.resolved_because says why:
    --             'not_found' (fixed on OLX) or 'listing_inactive' (the listing is gone);
    --             it goes back to open if the problem comes back
    --   ignored   Toni decided it isn't a problem; reruns leave it ignored
    status        text NOT NULL DEFAULT 'open' CONSTRAINT review_issue_status_check
                      CHECK (status IN ('open', 'resolved', 'ignored')),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),  -- the last run that found it
    resolved_at   timestamptz
);

-- The report (M2-3) lists open issues by priority.
CREATE INDEX IF NOT EXISTS review_issue_open_idx ON review_issue (priority) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS review_issue_listing_idx ON review_issue (listing_id);
