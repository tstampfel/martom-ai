-- 010: rolling refresh counters on `sync_run` (#135). The nightly sync re-fetches the
-- details of about 1/7 of the active listings (oldest `detail_fetched_at` first), so
-- every listing is refreshed within 7 nights; this replaced the weekly full run.
-- Re-runs safely (IF NOT EXISTS).
--
--   rolling_refreshed       rolling-slice detail GETs that succeeded this run
--   rolling_deferred        rolling-slice listings left for later because the run's other
--                           OLX requests used up the nightly request budget
--                           (OLX_NIGHTLY_REQUEST_BUDGET); a deferred slice never changes
--                           the status
--   oldest_detail_age_days  after the run: days since the oldest detail fetch among the
--                           active listings (first_seen_at for one never fetched); NULL
--                           when the run broke before it could look, or no listing is active
--   detail_refresh_overdue  oldest_detail_age_days is over 8: the alert (M6-3, #32)
--                           fires on it; it never changes the status either
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS rolling_refreshed integer NOT NULL DEFAULT 0;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS rolling_deferred integer NOT NULL DEFAULT 0;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS oldest_detail_age_days numeric(8, 2);
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS detail_refresh_overdue boolean NOT NULL DEFAULT false;
