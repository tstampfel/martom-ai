# Chat event contract (`POST /api/chat`)

`POST /api/chat` answers with a Server-Sent Events stream. Each SSE message carries one
event as JSON in its `data:` line, and the event's `type` field says which kind it is:

```text
data: {"type": "token", "text": "Odlično — "}

data: {"type": "done", "thread_id": "c0f3…"}
```

The endpoint itself lands with #21 [M4-4]. This document, the models and the schema are
the contract both sides build against.

## Where the contract lives

| File | Role |
| --- | --- |
| `src/martom/api/chat_events.py` | Source of truth: Pydantic models, one per event, joined in a union on `type` |
| `docs/api/chat-events.schema.json` | JSON Schema generated from the models; `tests/test_chat_events.py` fails when it is out of date |
| `web/src/api/events.ts` | TypeScript types, mirrored by hand from the schema |
| `web/src/api/fixtures/` | Recorded example streams; `events.test.ts` checks every event against the types and the schema |

After changing a model, regenerate the schema and update `events.ts` and the fixtures:

```sh
uv run python -m martom.api.chat_events
uv run pytest tests/test_chat_events.py
npm --prefix web test
```

## Rules for every event

- Every field is always present in the JSON; an empty value is `null` (or `[]` for lists).
  Unknown fields are not allowed.
- **Prices** are strings with exactly two decimals and a dot (`"53.00"`), in KM, never
  numbers. The frontend formats them for the customer (`53,00 KM`). A price of `null`
  means "cijena na upit". Prices only ever come from tools (database, BOM calculator).
- Customer-facing text inside events (`token`, step texts, questions, notes) is written by
  the advisor in Bosnian. Error texts are not sent: the frontend shows its own copy for each
  error `kind`.
- A reply always ends with exactly one `done` event, also after an `error`.

## Events

### `token`

A piece of the answer text. Append it to the advisor's current message.

```json
{"type": "token", "text": "Odlično — za 25 m dvorišta imamo dobra rješenja. "}
```

### `step`

A "Savjetnik radi" step. The same `id` is sent again when its `status` changes, so the
client updates that step in place. `status` is `working`, `done` or `needs_input` (the "?"
step that leads to clarify questions).

```json
{"type": "step", "id": "search", "text": "Pregledao 39 ograda", "status": "done"}
```

### `chips`

The clarify questions (`docs/mockups/*/2-clarify.html`). A `single` question offers
options to pick one from; a `side_lengths` question asks for the length of each fence side
in metres and has no options. An option's `value` goes back in the next message; `badge`
is shown on the chip and `note` under the question once the option is picked.

```json
{
  "type": "chips",
  "intro": "Još dvije stvari:",
  "questions": [
    {
      "id": "color",
      "text": "Koja boja?",
      "kind": "single",
      "options": [
        {"value": "antracit", "label": "Antracit (RAL 7016)", "badge": null, "note": null},
        {"value": "zelena", "label": "Zelena (RAL 6005)", "badge": null, "note": null},
        {"value": "any", "label": "Svejedno", "badge": null, "note": null}
      ]
    },
    {"id": "sides", "text": "Dužina svake strane (m)", "kind": "side_lengths", "options": []}
  ]
}
```

### `product_cards`

Products that fit (`docs/mockups/*/3-results.html`). `url` is always
`https://martom.olx.ba/artikal/{slug}`, and the slug ends in `listing_id`. `price` is the
price of the shown variant per `price_unit`; each variant has its own price.

```json
{
  "type": "product_cards",
  "cards": [
    {
      "listing_id": 63996799,
      "url": "https://martom.olx.ba/artikal/panelna-ograda-3d-nylofor-63996799",
      "title": "Panelna ograda 3D (Nylofor)",
      "subtitle": "Žica Ø 4,20/4,40 mm · plastificirana · antracit ili zelena",
      "why": "Čvrsta i uredna, ne treba je zatezati, a paneli od 250 cm daju najmanje stupova.",
      "badge": "Preporuka",
      "photo_url": null,
      "price": "53.00",
      "price_unit": "panel",
      "variants": [
        {"label": "103 × 250", "price": "38.00"},
        {"label": "153 × 250", "price": "53.00"},
        {"label": "153 × 200", "price": null}
      ]
    }
  ]
}
```

### `bom`

The materials list. Each line is what the BOM calculator (`src/martom/bom/`) produced,
with its price from the database: `item` and `size` identify the product, `name` is the
customer label. `item` is a BOM item key such as `panel`; it is a plain string in the
schema (the backend checks it against `martom.bom.common.Item`), so adding or removing a
BOM item doesn't change the contract. Use it only as a key.

A line with `price_on_request: true` has no `unit_price` and no `line_total` ("cijena na
upit"); `made_to_order: true` marks a "po narudžbi" line that Toni
confirms, and `label` holds its status text. The example is a shortened roll fence: the
100 cm support T-post has no price yet ("cijena na upit"). Prices are illustrative.

`total` is never a fake `"0.00"`; `unpriced_count` says how many lines are "na upit":

| Lines | `total` | `unpriced_count` | UI (copy lands in #74/#27) |
| --- | --- | --- | --- |
| All priced | sum of the lines | `0` | "Ukupno: 905,10 KM" |
| Some "na upit" | sum of the priced lines | number of "na upit" lines | "Ukupno: 905,10 KM + 2 stavke na upit" |
| All "na upit" | `null` | number of lines | "Ukupno: cijena na upit"; adding to the cart and ordering still work |

The models check rules the JSON Schema cannot express: `line_total` is
`unit_price × quantity`, `price_on_request` is true exactly when a line has no prices,
`total` is the sum of the priced lines (or `null` when there are none), and
`unpriced_count` equals the number of `price_on_request` lines. `web/src/api/events.test.ts`
checks the same rules on the fixtures.

```json
{
  "type": "bom",
  "title": "Popis materijala",
  "summary": "25 m · mreža u roli 100 cm · stupovi sa stopom",
  "lines": [
    {
      "item": "roll_welded",
      "name": "Varena ograda u roli 100 cm (25 m)",
      "size": "100 cm",
      "quantity": 1,
      "unit_price": "58.00",
      "line_total": "58.00",
      "price_on_request": false,
      "made_to_order": false,
      "label": null
    },
    {
      "item": "post_foot_plate",
      "name": "Stup sa stopom 100 cm",
      "size": "100 cm",
      "quantity": 14,
      "unit_price": "14.00",
      "line_total": "196.00",
      "price_on_request": false,
      "made_to_order": false,
      "label": null
    },
    {
      "item": "brace_t_post",
      "name": "Potporni stup 100 cm",
      "size": "100 cm",
      "quantity": 2,
      "unit_price": null,
      "line_total": null,
      "price_on_request": true,
      "made_to_order": false,
      "label": null
    }
  ],
  "total": "254.00",
  "unpriced_count": 1,
  "notes": ["Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. Dostava se potvrđuje."]
}
```

### `error`

The reply failed. `kind` is `rate_limited`, `invalid_request` or `internal`.
`retry_after_seconds` is set only for `rate_limited` (else `null`). A `done` event follows.

```json
{"type": "error", "kind": "rate_limited", "retry_after_seconds": 30}
```

### `done`

The last event of every reply. Send `thread_id` with the next message to continue the same
conversation.

```json
{"type": "done", "thread_id": "3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10"}
```
