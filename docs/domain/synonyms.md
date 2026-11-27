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
   hard-coded in the code.

## How to read and edit a rule

`- kolica: tačke, kariola` means: when a customer's question contains **kolica** (in any
form: "kolica", "kolicima"; accents don't matter), keyword search also finds listings that
say **tačke** or **kariola**. A rule works **one way only**. For a two-way pair, write two
lines.

- One word on each side of the colon; synonyms are separated by commas. No phrases: search
  matches single words, so "ručna kolica" would match every listing that says "ručna".
- Each word starts at most one rule.
- Only the lines under the heading below are read; a broken line stops the search with an
  error naming the line (and `uv run pytest` fails first).
- A rule widens what a word finds. Add one only when the word means the same thing to the
  customer, otherwise it brings back products they didn't ask for. Re-run the retrieval
  eval (`scripts/eval_retrieve.py`) after a change.

## Proširenja upita

<!-- Wheelbarrows. "kolica" in BiH means both a wheelbarrow and a hand truck (Tomislav,
     seed-12), so it expands to the wheelbarrow words. "tačke" means wheelbarrows only
     (seed-14), so the wheelbarrow words never expand back to "kolica": that would bring
     back the hand truck "KOLICA ZA TERET". -->
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
- stup: kolac
- kolac: stup

<!-- Fence mesh. "mreža" is broad (also nets and panels), so only "pletivo" expands to it;
     open question for Toni whether "mreža" should also find "pletivo". -->
- pletivo: mreža
