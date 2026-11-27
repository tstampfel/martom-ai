# Retrieval baselines

One row per eval run worth keeping, so later search changes (#15 hybrid, #16 rerank) compare
against real numbers. Run with:

```sh
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run python scripts/eval_retrieval.py
```

Metrics come from `martom.evals.retrieval`: recall@5 and recall@10 (share of a question's
expected listings in the top k), MRR (mean of 1 / rank of the first expected listing) and the
forbidden hit rate (share of questions with `must_not_include_listing_ids` that get one of those
ids in their top 10; lower is better). Off-topic questions (no expected ids) are left out of
recall and MRR.

| Date | Commit | Catalogue | Search mode | Eval set | Scored | recall@5 | recall@10 | MRR | Forbidden hit rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-27 | b3b1dcf (Tomislav's run) | 2,570 active listings, extraction run 4 | keyword | 11 lines (seed-01 … seed-11) | 10 | 0.850 | 0.900 | 0.694 | n/a |
| 2026-11-27 | branch m3-2b-eval-cases @ bcb165e (on top of bfce370, after #15) | 2,570 active listings, extraction run 4 | keyword | 14 lines (+ seed-12 kolica, seed-13 panelan, seed-14 građevinska kolica) | 13 | 0.737 | 0.814 | 0.698 | 0.000 (0 of 2) |
| 2026-11-27 | #15 (PR #110), Tomislav's comparison | martom_m33 (copy of the 2,570-listing catalogue, embedded with Cohere embed-multilingual-v3.0) | vector | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.950 | n/a |
| 2026-11-27 | #15 (PR #110), Tomislav's comparison | martom_m33 (same) | hybrid | 11 lines (seed-01 … seed-11) | 10 | 0.900 | 0.950 | 0.933 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | hybrid (same run, baseline) | 11 lines (seed-01 … seed-11) | 10 | 0.900 | 0.950 | 0.933 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | hybrid → rerank-v4.0-fast, no parser | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.950 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | full: Haiku filters → hybrid → rerank-v4.0-fast, batch parser (no time limit), 0/11 parse fallbacks | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.950 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | full, rerank-v4.0-fast, live parser (1.5 s), 4/11 parse fallbacks | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.950 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | hybrid → rerank-v5.0-fast, no parser | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.900 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | full: Haiku filters → hybrid → rerank-v5.0-fast, batch parser (no time limit), 0/11 parse fallbacks | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.900 | n/a |
| 2026-11-27 | #16, branch m3-4-query-rerank @ dcd5ae4 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | full, rerank-v5.0-fast, live parser (1.5 s), 1/11 parse fallbacks | 11 lines (seed-01 … seed-11) | 10 | 0.950 | 0.950 | 0.900 | n/a |
| 2026-11-27 | #16 final, m3-4-query-rerank @ e5cdbd8 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | hybrid (same run, baseline) | 14 lines (seed-01 … seed-14) | 13 | 0.814 | 0.917 | 0.910 | not computed (eval_retrieve.py) |
| 2026-11-27 | #16 final, m3-4-query-rerank @ e5cdbd8 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | hybrid → rerank-v5.0-fast, no parser | 14 lines (seed-01 … seed-14) | 13 | 0.891 | 0.891 | 0.872 | not computed (eval_retrieve.py) |
| 2026-11-27 | #16 final, m3-4-query-rerank @ e5cdbd8 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | **M3-4 baseline**: full, Haiku filters → hybrid → rerank-v5.0-fast, batch parser (no time limit), 0/14 parse fallbacks | 14 lines (seed-01 … seed-14) | 13 | 0.891 | 0.891 | 0.872 | not computed (eval_retrieve.py) |
| 2026-11-27 | #16 final, m3-4-query-rerank @ e5cdbd8 (Tomislav's run) | 2,570 active listings, extraction run 4, embedded with embed-multilingual-v3.0 | **M3-4 baseline**: full, rerank-v5.0-fast, live parser (2.5 s), 0/14 parse fallbacks | 14 lines (seed-01 … seed-14) | 13 | 0.891 | 0.891 | 0.872 | not computed (eval_retrieve.py) |

Vector and hybrid runs need Cohere embeddings and `COHERE_API_KEY`, so Tomislav runs them by
hand. **Tomislav only:** agents never pass `--env-file .env` (CLAUDE.md); an agent that needs a
vector or hybrid row asks Tomislav for it. The 14-line rows come from his main database, run
from the root of the main checkout (migration 007, on `main` since #15, adds the embedding
columns):

```sh
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run martom migrate
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run --env-file .env martom embed
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run --env-file .env python scripts/eval_retrieval.py --search hybrid
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run --env-file .env python scripts/eval_retrieval.py --search vector
```

- `martom embed` embeds every active product and knowledge chunk the first time: about 28
  Cohere batch calls of 96 texts, roughly 0.2 M tokens at $0.10 per million, so under $0.05
  (#15's run; free on the trial key). #15 recorded no duration. Later runs only re-embed
  changed rows. Each eval run adds one query embedding per question (14).
- `EMBEDDING_PROVIDER` defaults to `cohere` and `EMBEDDING_MODEL` to
  `embed-multilingual-v3.0` (`Settings`, `martom.embeddings`), so neither is passed, as long as
  `.env` doesn't set them to something else.
- `--env-file` only fills variables that aren't set already, so the inline `DATABASE_URL` wins
  over any `DATABASE_URL` in `.env`.

## Rerank comparison (#16, 2026-11-27)

`scripts/eval_retrieve.py --rerank-models rerank-v4.0-fast,rerank-v5.0-fast`, Tomislav's run on
his main database at dcd5ae4 (on the 11-line set, before the rebase onto #102's 14 lines). Both
models saw the same questions, the same 30 candidates and the same Haiku filters. Latency is
the live timed run: 33 searches per model (11 questions × 3), live timeouts and fallbacks on,
the two models taking turns on each question.

| Rerank model | recall@5 | recall@10 | MRR | Live parse fallbacks (scoring / timed) | Rerank fallbacks | p95 rerank | p95 total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rerank-v4.0-fast | 0.950 | 0.950 | 0.950 | 4/11 / 10/33 | 0/33 | 737 ms | 2,274 ms |
| rerank-v5.0-fast | 0.950 | 0.950 | 0.900 | 1/11 / 6/33 | 0/33 | 704 ms | 2,227 ms |

- **Decision (Tomislav):** the two tie. The MRR gap is one question (`seed-09-no-height-kokosi`:
  v5.0 puts 73950900 first and the expected 68378185 second), which is noise at n = 10. By his
  rule (equal within noise → the newer, cheaper model) the default is now `rerank-v5.0-fast`;
  `RERANK_MODEL=rerank-v4.0-fast` still works.
- **Recheck** both models with Toni's questions from #13 when they arrive, on the full eval set.
- The two models score on different scales: best relevance on topic was 0.86–0.92 with
  v4.0-fast and 0.71–0.79 with v5.0-fast; the off-topic question `seed-10` got 0.106 and 0.004.
  Any "nothing relevant" threshold in M4 grading must be set for the model in use.
- #102 checks, full pipeline: "kolica" doesn't return the grinder 52889522 with either model, and
  "panelan" puts the panels 63996799 and 1199211 first with both.
- **Parser limit:** Haiku alone took p50 1,330 ms and p95 1,599 ms (no time limit; 3 of 11 over
  1.5 s), so the live 1.5 s limit sent 6–10 of 33 timed searches (18–30%) out without filters.
  The understand stage (parse and embedding in parallel) was p95 ~1,504 ms, i.e. the limit. The
  limit is now 2.5 s, and the embedding and the unfiltered search run during the parse, so the
  live rows and the latency above are from **before** that change and should be re-run.
- **Latency target:** p95 total ~2.2 s missed the plan's original "p95 retrieval < 800 ms", because
  the Haiku parse alone is p50 1.3 s on the critical path. Tomislav's decision (27 Nov): the M3-4
  target is now **p95 retrieval < 2.5 s with Haiku on the path**, rechecked when the Redis cache
  lands (M3-5); docs/PLAN.md says so. The latency above predates 2967f5a (2.5 s parse limit,
  embedding and unfiltered search during the parse); fresh numbers follow from his run on the
  final commit. Recall@5 0.95 on this 10-question set met "≥ 90%"; the final 14-question run is
  0.891, just under it, accepted by Tomislav (see M3-4 baseline below).

## M3-4 baseline (#16 final, 2026-11-27)

`scripts/eval_retrieve.py` (default `rerank-v5.0-fast`), Tomislav's run on his main database at
e5cdbd8: live parse limit 2.5 s, the embedding and the unfiltered search running during the
parse. **14 questions (13 scored) against 10 scored before, so the numbers are not comparable
1:1 with the #16 comparison above**: seed-12, seed-13 and seed-14 are new and hard.

| Measure | Result | Target (docs/PLAN.md) |
| --- | --- | --- |
| recall@5 / recall@10 / MRR (batch = live parser) | 0.891 / 0.891 / 0.872 | recall@5 ≥ 90% |
| Parse fallbacks | 0/14 scoring, 0/42 timed (was 6–10 of 33 at 1.5 s) | |
| Haiku parse alone | p50 1,191 ms, p95 1,823 ms, max 1,823 ms; 0/14 over 2.5 s | |
| Timed run, 42 searches (14 × 3) | total p50 1,686 ms, **p95 2,209 ms**, max 2,637 ms; rerank p95 512 ms; search after the parse p95 50 ms | p95 retrieval < 2.5 s with Haiku on the path |
| Rerank fallbacks | 0/42 | |

- Batch and live parser give the same recall, so the 2.5 s limit loses no filters on this set.
- The search stage after the parse is 0 ms (p50) for questions without filters: their results
  are the unfiltered search that already ran during the parse.
- recall@5 0.891 is just under 0.90 (recall is a mean per question: one more expected listing in
  seed-14's top 5 would give 0.917). The misses are below; seed-06 is a data issue, and seed-12 /
  seed-14 come from listing wording.
- #102, full pipeline: "kolica" top 5 has no grinder 52889522; "panelan" puts the panels 63996799
  and 1199211 first (relevance 0.30 and 0.27, low for v5.0 on a one-word typo).

### Misses (full pipeline, rerank-v5.0-fast)

- `seed-06-anker-m8x80` (R@10 0.50): 40021189 "TIPLO SIDRO ANKER ANKERI SVE MJERE" is "Na upit"
  with no sizes in the description, so nothing ties it to "M8x80". **Data issue**; Toni has been
  asked to add sizes and prices.
- `seed-12-kolica` (R@10 0.75): 47382791 "KARIOLA KARIOLE TAČKE CIVARE CIVARA" never says "kolica",
  so neither keyword nor vector search reaches it; the other three kolica listings are found.
  Listing wording (a title with "kolica" would fix it, like 53055264 and 57362536).
- `seed-14-gradjevinska-kolica` (R@5 0.33, R@10 0.33, RR 0.33): the reranker puts other
  "građevinsk…" products first (kolotura 52849505, građevinski konac 54711855), with the wheelbarrow
  53055264 3rd; 57362536 "KARIOLA KOLICA 75L" and 47382791 are not in the top 10. Hybrid alone had
  R@10 0.67 here, so the rerank costs one listing on this question. The forbidden hand truck
  35992113 and grinder are not in the top 5.
- Ranked second, not missed: `seed-04-t-stup` (RR 0.50: 81645615 "STUP … ZA STOPOM 40X40MM … 150CM",
  also a 150 cm fence post, comes before the T post 59473597) and `seed-09-no-height-kokosi`
  (RR 0.50: the PVC chicken mesh 73950900 before 68378185). Both first hits are plausible answers;
  worth asking Toni whether they should count as expected.

## Weak cases

### seed-06 is a data issue, not a search bug (2026-11-27)

`seed-06-anker-m8x80` misses 40021189 because that listing is "Na upit" with no sizes in the
description; Toni has been asked to add sizes and prices. It stays at R@10 0.50 in every mode
(keyword, vector, hybrid, rerank, both rerank models) until the listing changes.

### Vector and hybrid, 11-line set (2026-11-27, Tomislav's comparison)

- n = 10 scored questions, so each difference between rows is one question.
- `seed-06-anker-m8x80`: R@10 0.50 in every mode and for every embedding model compared; the
  anchor listing 40021189 is still missing from the top 10.
- The #102 checks (`issue_102_hits`, hybrid): "kolica" no longer returns the grinder, and
  "panelan" still finds the panels.

### Keyword, 11-line set (2026-11-27)

Tomislav's run and a re-run for #102 gave the same numbers.

- `seed-08-typo-panel` ("panelan ogarda antracit"): R@5 0.00, R@10 0.50, RR 0.11. Only 1199211
  makes the top 10, in 9th place.
- `seed-06-anker-m8x80`: R@5 0.50. The panel listings outrank the anchor listing 40021189, which
  is missing from the top 10.
- `seed-05-zatezac`: RR 0.33. The BOM tensioner 18129143 comes 3rd.
- `seed-01-panel-153-dvoriste`: RR 0.50. Garden Light (25070880) comes first.

### Keyword, 14-line set (2026-11-27)

The four cases above, plus:

- `seed-13-typo-panelan` ("panelan"): R@5 0.00, R@10 0.50, RR 0.12. Gates, gabion panels and dog
  houses "od panela" come first; 1199211 is 8th and 63996799 is missing from the top 10.
- `seed-12-kolica` ("kolica"): R@5 0.75, RR 1.00. The wheelbarrow listing 47382791 (titled "KARIOLA KARIOLE TAČKE
  CIVARE", no "kolica" word) is not returned at all; keyword search can't link "kolica" to
  "kariola" or "tačke". The grinder 52889522 is **not** returned on this catalogue: its run 4
  search text reads "... total | kutna brusilica | ...", so `word_similarity('kolica', …)` is
  0.43, under the 0.55 threshold. The 0.57 false positive from #101 comes from the recorded
  fixture text, where "brusilica kutna" sits next to each other. The case stays as a guard,
  because a re-extraction could write the words in that order again.
- `seed-14-gradjevinska-kolica` ("građevinska kolica"): R@5 0.33, RR 1.00. Wheelbarrow 53055264 comes first,
  then other "građevinsk…" products (builder's line, plastic sheeting, crowbar); 57362536 and
  47382791 are not in the top 10. The hand truck 35992113 and the grinder 52889522 are not
  returned, so the forbidden check passes.
