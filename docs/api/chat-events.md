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
  "intro": "Još tri stvari:",
  "questions": [
    {
      "id": "posts",
      "text": "Gdje idu stupovi?",
      "kind": "single",
      "options": [
        {"value": "plate", "label": "Na beton, s pločicom", "badge": null, "note": null},
        {
          "value": "concrete",
          "label": "U zemlju (betoniram)",
          "badge": "po narudžbi, 10–15 dana",
          "note": "Stupovi za betoniranje su po narudžbi, 10–15 dana. Beton ne prodajemo, nabavljate ga sami."
        },
        {"value": "unknown", "label": "Ne znam", "badge": null, "note": null}
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
customer label. A line with `price_on_request: true` has no `unit_price` and no
`line_total` ("cijena na upit"); `made_to_order: true` marks a "po narudžbi" line that Toni
confirms, and `label` holds its status text.

The models check two rules the JSON Schema cannot express: `line_total` is
`unit_price × quantity`, and `total` is the sum of the priced lines (lines "na upit" are
left out).

```json
{
  "type": "bom",
  "title": "Popis materijala",
  "summary": "25 m · panel 153 cm · antracit",
  "lines": [
    {
      "item": "panel",
      "name": "Panel 153 × 250 cm, antracit",
      "size": "153 × 250 cm",
      "quantity": 10,
      "unit_price": "53.00",
      "line_total": "530.00",
      "price_on_request": false,
      "made_to_order": false,
      "label": null
    },
    {
      "item": "post_concrete",
      "name": "Stup za betoniranje 125 cm",
      "size": "125 cm",
      "quantity": 11,
      "unit_price": null,
      "line_total": null,
      "price_on_request": true,
      "made_to_order": true,
      "label": "po narudžbi, 10–15 dana"
    }
  ],
  "total": "530.00",
  "notes": ["Beton ne prodajemo, nabavljate ga sami."]
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
