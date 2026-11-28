# evals/: hand-written eval cases only

This folder is in a public repo. It holds only eval cases that a person wrote or reviewed
by hand. **A real customer chat is never committed here, not even a scrubbed one**
(Tomislav, 28 Nov 2026).

| File | What it holds |
| --- | --- |
| `retrieval.jsonl` | The retrieval eval set: one question per line, with the listings (or manual products) search should return. `martom.evals.retrieval` reads it. |
| `baselines.md` | One row per eval run worth keeping, so later search changes compare against real numbers. |

## Adding a case

1. **Write it by hand.** A case may start from something a customer asked, but you rewrite
   it in your own words. No real customer wording, name, phone, e-mail, street, house number
   or village stays in it. A town everyone knows (Čitluk, Mostar) is fine when the question
   needs one.
2. **Get it approved.** Tomislav or Toni approves every case before it is added.
3. **Say where it came from and who approved it.** Every new line carries:
   - `source`: `seed` (drafted from real OLX listings, not from a chat), `toni` (Toni's
     questions, #13) or `tomislav`;
   - `approved_by`: `tomislav` or `toni`.

   The 15 `seed` cases from before 28 Nov 2026 have no `approved_by`: they are older than
   this rule, and none of them comes from a chat.

Example line (fields as `martom.evals.retrieval.load_questions` reads them):

```json
{"id": "toni-01-panel-antracit", "question": "Imate li panele u antracitu za ogradu od 1,5 m?", "expected_listing_ids": [63996799], "source": "toni", "approved_by": "toni", "note": "Colour filter."}
```

## Real chats go to the private export, never here

To study what customers really asked, export the chats with `martom export-chats`. It passes
every message through `martom.evals.scrub.scrub_personal_data` and writes only to the
private S3 bucket (in the EU, objects deleted within 90 days) or to a folder outside every git
checkout. It refuses a folder inside this repo. See `infra/README.md`, "Exporting chats". The
scrub misses some things (its docstring lists them), so read an export before you use it, and
turn anything worth keeping into a hand-written case as above.

## What CI checks

`tests/test_fixture_privacy.py` scans every file here and under `tests/fixtures/`. It fails on:

- a phone number (only Martom's public 036 651 427 and the fake 060 000 000 pass);
- an e-mail address (only `martom-bekaert.com` passes);
- a street address: a street word with a house number or "bb", a delivery or living phrase
  with a house number, an address label, or a postal code with its town. It uses the scrub's
  own patterns (`address_matches`), so the check and the scrub can't drift. Product sizes
  ("153 x 250", "Panel 3D 153 cm") pass, and so does Martom's own shop address;
- seller or customer fields, files over 100 KB and files with more than 20 listings.
