-- 002: `olx_listing` (one row per OLX listing of the shop) and `sync_run` (one row per
-- sync run, for freshness alerts). Filled by `martom sync` (M1-4).
-- Every statement re-runs safely (IF NOT EXISTS / OR REPLACE).

CREATE TABLE IF NOT EXISTS olx_listing (
    id                 bigint PRIMARY KEY,          -- the OLX listing id
    -- From the listing-page row (GET /users/{shop}/listings), refreshed on every run.
    slug               text,                        -- link: https://martom.olx.ba/artikal/{slug}
    title              text,
    category_id        integer,
    top_category_id    integer,
    summary            jsonb NOT NULL,              -- the listing-page row as OLX sent it
    -- From GET /listings/{id}, refreshed only when the detail is fetched again.
    short_description  text,
    description_html   text,                        -- as OLX sent it
    description_text   text,                        -- footer stripped: what extraction reads
    price              numeric(12, 2),
    display_price      text,
    price_by_agreement boolean,
    state              text,                        -- "new" / "used", as OLX says
    status             text,                        -- "active", ... as OLX says
    available          boolean,
    brand              text,
    attributes         jsonb,                       -- [{"name": ..., "value": ...}, ...]
    -- Change detection. summary_hash is written only together with a fetched detail, so a
    -- NULL or an old value means "fetch the detail again on the next run".
    summary_hash       text,
    detail_hash        text,                        -- M2-1 re-extracts when this changes
    -- Inactive listings are never deleted. Search and the BOM read `active_olx_listing`.
    active             boolean NOT NULL DEFAULT true,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    detail_fetched_at  timestamptz,
    deactivated_at     timestamptz
);

CREATE INDEX IF NOT EXISTS olx_listing_active_idx ON olx_listing (active);

-- The listings a customer may see. Search (M3) and the BOM (M4) query this view, never
-- the table, so a sold listing can't show up in an answer.
-- Postgres expands `*` once, when the view is created: any later migration that adds
-- columns to olx_listing must also re-create this view (DROP VIEW + CREATE VIEW), or
-- the new columns won't show in it.
CREATE OR REPLACE VIEW active_olx_listing AS
    SELECT * FROM olx_listing WHERE active;

CREATE TABLE IF NOT EXISTS sync_run (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind                    text NOT NULL CONSTRAINT sync_run_kind_check
                                CHECK (kind IN ('nightly', 'full', 'apply_skipped')),
    -- Started by the scheduler (`martom sync --scheduled`, M6-1), not by hand. Only
    -- scheduled nightly/full runs count toward the automatic apply of skipped ids.
    scheduled               boolean NOT NULL DEFAULT false,
    -- How the run ended. The freshness alert (M6-3) fires on anything but 'succeeded'.
    --   running       the run is in progress (or crashed hard: no finished_at)
    --   succeeded     a clean run: 100% coverage, no guard tripped
    --   partial       coverage < 100% (nothing was deactivated), a detail fetch or GET
    --                 re-check still failed after its retries, or OLX stopped the run
    --                 early (a Retry-After over 1 h, or the 3 h run deadline); the ids
    --                 still to do are in failed_listing_ids
    --   skipped_guard the 10% guard or the 20% re-check guard skipped deactivation
    --   failed        the run broke (an OLX or database error)
    -- Precedence when several apply: failed > skipped_guard > partial > succeeded.
    -- (A guard needs deactivation candidates, which need 100% coverage, so a sync run
    -- can't really be both partial and skipped_guard; the order is fixed anyway.)
    status                  text NOT NULL DEFAULT 'running' CONSTRAINT sync_run_status_check
                                CHECK (status IN (
                                    'running', 'succeeded', 'partial', 'skipped_guard', 'failed'
                                )),
    started_at              timestamptz NOT NULL DEFAULT now(),
    finished_at             timestamptz,
    shop_total              integer,                 -- listings OLX reports for the shop
    coverage                numeric(7, 4),           -- unique listings seen / shop_total
    seen                    integer NOT NULL DEFAULT 0,
    new                     integer NOT NULL DEFAULT 0,
    fetched                 integer NOT NULL DEFAULT 0,  -- detail GETs that succeeded
    changed                 integer NOT NULL DEFAULT 0,  -- known listings whose detail hash changed
    deactivation_candidates integer NOT NULL DEFAULT 0,
    deactivated             integer NOT NULL DEFAULT 0,
    reactivated             integer NOT NULL DEFAULT 0,
    errors                  integer NOT NULL DEFAULT 0,  -- GETs still failing after retries
    -- GETs that failed at first but succeeded on a retry: they don't change the status,
    -- but a rising count shows OLX getting less reliable.
    retried_count           integer NOT NULL DEFAULT 0,
    -- Listing ids whose detail fetch or re-check still failed after its retries, or that
    -- a run OLX stopped early did not get to; the next nightly/full run fetches and
    -- re-checks these first.
    failed_listing_ids      bigint[],
    -- The gap between OLX requests (seconds) when the run ended: 2.0 by default (or
    -- OLX_REQUEST_INTERVAL), raised by 50% after each 429, up to 6.0.
    final_request_interval  numeric(5, 2),
    -- 10% guard: the run would have deactivated more than 10% of the active listings, so
    -- it deactivated none. The alert (M6-3) queries this flag.
    deactivation_skipped    boolean NOT NULL DEFAULT false,
    would_deactivate        integer,
    -- The listing ids the skipped run confirmed as gone (OLX said 404 or inactive), kept so
    -- a later manual override can apply them after a human has checked them.
    skipped_listing_ids     bigint[],
    -- The skipped runs whose ids this run applied: one id for `martom sync --apply-skipped`
    -- (kind 'apply_skipped'); for an automatic apply, this run and the skipped run before it.
    applied_run_ids         bigint[],
    -- 20% re-check guard: the GET re-check before applying skipped ids failed (network
    -- error, timeout, 429, 5xx, ...) for more than 20% of them, so nothing was applied.
    -- Alerted on like `deactivation_skipped` (M6-3).
    recheck_skipped         boolean NOT NULL DEFAULT false,
    error                   text                      -- why a failed run failed
);

CREATE INDEX IF NOT EXISTS sync_run_started_at_idx ON sync_run (started_at DESC);
