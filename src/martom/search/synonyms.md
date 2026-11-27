# Regional synonyms for search

> **Status: DRAFT (#121), not yet reviewed.** Tomislav and Toni review every rule below
> before this is treated as confirmed. Until then the search uses the draft as it is.

Customers and OLX listings use different regional (BiH/HR) words for the same product. A
customer asks for "kolica", the wheelbarrow listing says "KARIOLA KARIOLE TAČKE CIVARE".
Two things close that gap:

1. **At extraction** (`martom extract`), Claude Haiku returns common BiH/HR synonyms for each
   product in its own field (`regional_synonyms`), and Python appends them to the listing's
   `search_text`. Customers never see them. Nothing to review here: they change with each
   extraction, and the retrieval eval set checks their effect.
2. **At search time**, keyword search expands the customer's words with the rules below.
   This list is the reviewed part: `src/martom/search/synonyms.py` reads it, and nothing is
   hard-coded in the code. It lives next to that code (package data), so every install and
   the production image ship it; `docs/domain/synonyms.md` only points here.

## How to read and edit a rule

`- kolica: tačke, kariola` means: when a customer's question contains **kolica**, or a form
of it that search stems the same way ("kolicima"; accents don't matter, so "tacke" counts as
"tačke"), keyword search also finds listings that say **tačke** or **kariola**. Not every
form stems the same: "stupovi" stems to "stupov", not "stup", so a plural like that is
listed on the left too: `- stup, stupovi: kolac`. A rule works **one way only**. For a
two-way pair, write two lines.

- Single words only (letters and digits), separated by commas; several words on the left
  share the synonyms. No phrases and no "-" or "/": search matches single words, so
  "ručna kolica" would match every listing that says "ručna".
- Each word starts at most one rule (with or without accents: "tacke" and "tačke" are the
  same word to search).
- Only the lines under the heading below are read. Every non-blank line there must be a
  rule; comments go in `<!-- -->` (they may span lines, and comment out rules). A line that
  is neither, or a section with no rules, is an error naming the line: `uv run pytest`
  fails, and the running search logs a warning and searches without expansion until it is
  fixed.
- A rule widens what a word finds. Add one only when the word means the same thing to the
  customer, otherwise it brings back products they didn't ask for. Re-run the retrieval
  eval (`scripts/eval_retrieve.py`) after a change.

## Proširenja upita

<!-- Wheelbarrows. "kolica" in BiH means both a wheelbarrow and a hand truck (Tomislav,
     seed-12), so it expands to the wheelbarrow words. "tačke" means wheelbarrows only
     (seed-14), so the wheelbarrow words never expand back to "kolica": that would bring
     back the hand truck "KOLICA ZA TERET".
     Open question for Toni: "tačke" stems like "tačka" (a point, a dot), so a question
     with "tačka" also triggers the wheelbarrow rule. -->
- kolica: tačke, kariola, karijola, civare
- tačke: kariola, karijola, civare
- kariola: tačke, karijola, civare
- karijola: tačke, kariola, civare
- civare: tačke, kariola, karijola

<!-- Tensioners for fence wire. -->
- španer: zatezač
- zatezač: španer

<!-- Posts. Open question for Toni: a "kolac" is usually a wooden stake; should a
     customer's "stup" also find stakes, or only metal posts? -->
- stup, stupovi: kolac
- kolac: stup, stupovi

<!-- Fence mesh. "mreža" is broad (also nets and panels), so only "pletivo" expands to it;
     open question for Toni whether "mreža" should also find "pletivo". -->
- pletivo: mreža
