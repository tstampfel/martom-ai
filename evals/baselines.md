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
| 2026-11-27 | branch m3-2b-eval-cases @ 07e2164 (on top of b3b1dcf) | 2,570 active listings, extraction run 4 | keyword | 13 lines (+ seed-12 kolica, seed-13 panelan) | 12 | 0.771 | 0.854 | 0.672 | 0.000 (0 of 1) |

## Weak cases

### Keyword, 11-line set (2026-11-27)

Tomislav's run and a re-run for #102 gave the same numbers.

- `seed-08-typo-panel` ("panelan ogarda antracit"): R@5 0.00, R@10 0.50, RR 0.11. Only 1199211
  makes the top 10, in 9th place.
- `seed-06-anker-m8x80`: R@5 0.50. The panel listings outrank the anchor listing 40021189, which
  is missing from the top 10.
- `seed-05-zatezac`: RR 0.33. The BOM tensioner 18129143 comes 3rd.
- `seed-01-panel-153-dvoriste`: RR 0.50. Garden Light (25070880) comes first.

### Keyword, 13-line set (2026-11-27)

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
