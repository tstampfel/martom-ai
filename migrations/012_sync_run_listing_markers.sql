-- 012: raw OLX marker counts on `sync_run` (#179). OLX renews a featured ("Izdvojeno")
-- listing every 8 h or 24 h and a normal one every 7 days; we count, per nightly/full
-- run, the listings whose listing-page row carries each marker, so the trend is visible.
-- The columns are named after the OLX fields, not "featured": which field means
-- "Izdvojeno" is unconfirmed (Toni says 22470828 isn't featured, yet OLX sends
-- sponsored = 1 for it). Re-runs safely (IF NOT EXISTS).
--
--   sponsored_count  unique listings seen this run whose row has `sponsored` true or > 0
--   pinned_count     unique listings seen this run whose row has `pinned` true or > 0
--
-- Both count only the listings the run saw: read them together with `coverage` (a run
-- whose paging stopped early without an error, e.g. a clamped or empty page, saw fewer).
-- Both are NULL when the run has no listing pages at all (an apply_skipped run, or a run
-- that OLX stopped or failed while paging). Neither ever changes the status.
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS sponsored_count integer;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS pinned_count integer;
