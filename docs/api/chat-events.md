# Chat contract (`POST /api/chat`)

The browser sends one JSON request body per customer message, and `POST /api/chat` answers with a Server-Sent Events stream. Each SSE message carries one
event as JSON in its `data:` line, and the event's `type` field says which kind it is:

```text
data: {"type": "token", "text": "Odlično — "}

data: {"type": "done", "thread_id": "c0f3…"}
```

The endpoint is `src/martom/api/chat.py` (#21 [M4-4]); "How the endpoint behaves" below
covers threads, errors and limits. This document, the models and the schema are the
contract both sides build against.

## How the endpoint behaves

- **Response.** Always `200` with `Content-Type: text/event-stream`, `Cache-Control:
  no-store` and `X-Accel-Buffering: no`, also for an invalid request: every failure is an
  `error` event followed by `done`, so the browser has one code path.
- **Order.** `step` "parse" `working` comes first, at once. While the advisor works,
  steps follow its progress (parse done, search working/done, bom done). Then the reply:
  for questions, the `clarify` step (`needs_input`), `token`s and `chips`; otherwise
  `token`s, `product_cards` (when the advisor looked products up this turn) and `bom`
  (only when this turn made a new list). `done` is always last.
- **Tokens.** The answer is streamed only after the grounding check passed (every price and
  number comes from a tool). The `token` events are the checked answer cut into chunks of
  a few words, not the model's live tokens: join them in order and you have the answer.
- **Product cards** come only from the products the advisor looked up this turn (database
  or `manual_products.toml` prices). `why` is `""` for now (no grounded "why it fits"
  yet), and `badge`, `subtitle` and `photo_url` are `null`.
- **Threads.** The server issues every `thread_id` (256 random bits); send it back to
  continue. An id that is missing, malformed or has no saved conversation starts a new
  thread, and the `done` event carries the new id. A client never picks its own id.
- **One turn at a time.** A second message on a thread whose previous turn still runs
  waits for it (up to 60 s, then `error` kind `internal`).
- **Invalid requests** (bad JSON, a body the request schema refuses, a body over 16 KB, a
  message over 2,000 characters, more than 50 sides, more than 10 choices or a choice key
  or value over 64 characters) answer `error` kind `invalid_request` and `done`, with the sent `thread_id`
  only if it names a saved conversation, else a fresh id; nothing is saved.
- **No advisor** (the API runs without `ANTHROPIC_API_KEY` or an embedder): every message
  answers `error` kind `internal` and `done`.
- **Not sent yet:** cart additions and the "contact Martom" offer have no event yet
  (#147), and neither does the roll fence's suggested wire add-on.

## Where the contract lives

| File | Role |
| --- | --- |
| `src/martom/api/chat_events.py` | Source of truth for the events: Pydantic models, one per event, joined in a union on `type` |
| `src/martom/api/chat_request.py` | Source of truth for the request body (`ChatRequest`) |
| `docs/api/chat-events.schema.json`, `docs/api/chat-request.schema.json` | JSON Schemas generated from the models; `tests/test_chat_events.py` and `tests/test_chat_request.py` fail when they are out of date |
| `web/src/api/events.ts`, `web/src/api/chatClient.ts` | TypeScript types, mirrored by hand from the schemas |
| `web/src/api/fixtures/` | Recorded example streams; `events.test.ts` checks every event against the types and the schema, `chatClient.test.ts` checks requests the clarify form builds |

After changing a model, regenerate the schemas and update the TypeScript types and the fixtures:

```sh
uv run python -m martom.api.chat_events   # writes both schemas
uv run pytest tests/test_chat_events.py tests/test_chat_request.py
npm --prefix web test
```

## Request

`message` is what the customer typed, or the summary of their chip answers ("2 kuta ·
10 + 5 + 10 m · Na beton, s pločicom · Antracit (RAL 7016)"); it is display text, and
the agent never parses lengths out of it. `thread_id` comes from the previous reply's
`done` event (`null` for the first message). `answers` is set only when the message
answers a `chips` event, else `null`:

- `choices`: question id → picked option `value`, for each `single` question answered.
- `sides`: the length of each fence side in metres, as numbers, in order. Each side is
  above 0 and at most 300 m (longer sides get no BOM, so the request is refused; the
  form checks it first). Empty when the chips had no `layout` question, or the customer
  picked "Ne znam" for the corners; the length then comes from the message. A straight
  fence (0 corners) sends `project.length_m` as its one side.
- `closed`: `true` when the fence goes all the way round. A closed fence needs at least
  3 sides; with no sides it is `false`.

The corners are never sent: they are `len(sides) - 1` for an open fence and
`len(sides)` for a closed one (`ClarifyAnswers.corners`, the same rule as the BOM's
`check_layout`). Unknown fields are refused.

```json
{
  "message": "2 kuta · 10 + 5 + 10 m · Na beton, s pločicom · Antracit (RAL 7016)",
  "thread_id": "3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10",
  "answers": {
    "choices": {"posts": "plate", "color": "antracit"},
    "sides": [10, 5, 10],
    "closed": false
  }
}
```

Around a yard with four sides:

```json
{
  "message": "4 kuta, zatvoreno · 10 + 5 + 10 + 5 m · Na beton, s pločicom · Svejedno",
  "thread_id": "3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10",
  "answers": {"choices": {"posts": "plate", "color": "any"}, "sides": [10, 5, 10, 5], "closed": true}
}
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

The clarify questions and the "Vaš projekt" summary (`docs/mockups/*/2-clarify.html`).

`project` (or `null`) fills the dark panel before the customer answers: a short `title`
("Ograda za dvorište"), the total `length_m` the advisor understood from the message
(or `null`), and `facts` it already knows, each a `label` and a `value` ("Visina", "oko
150 cm"). `panel_order` lists single question ids in the order the panel shows their
rows, which can differ from the form's order (the canvas asks posts before colour but
lists Boja before Stupovi); questions not listed follow in question order. Without a
`project` the panel falls back to the customer's first message as its title.

`length_m` may be over 300 m (a long fence with corners is valid). The form never sends
it back as one side above 300 m: with 0 corners it shows the "too long" hint instead
and keeps the submit button disabled.

Each question has the full `text` for the form and an optional short `label` for its
panel row ("Stupovi"; the panel uses `text` when it is `null`). There are two kinds:

- `single`: pick one option. An option's `value` goes back in `answers.choices`; `badge`
  is shown on the chip, `note` under the question once the option is picked, and
  `short_label` (or `label`) in the panel. `default` is an option value picked before
  the customer taps anything, or `null`; the advisor sends `"plate"` for the posts
  question, because base-plate posts are the default (fence-rules.md).
- `layout`: the fence layout. It has no options and no default; the frontend draws the
  corner chips (0, 1, 2, 3, 4+, Ne znam), the "Ide okolo (zatvoreno)" toggle and one
  length field per side (none for 0 corners when `project.length_m` is known, none for
  "Ne znam"), and sends the result as `answers.sides` and `answers.closed` (see Request).
  At most one per event.

Question ids are unique within the event.

```json
{
  "type": "chips",
  "intro": "Još tri stvari:",
  "project": {
    "title": "Ograda za dvorište",
    "length_m": 25,
    "facts": [{"label": "Visina", "value": "oko 150 cm"}],
    "panel_order": ["color", "posts"]
  },
  "questions": [
    {"id": "layout", "text": "Koliko ima kutova?", "label": "Kutovi", "kind": "layout", "options": [], "default": null},
    {
      "id": "posts",
      "text": "Gdje idu stupovi?",
      "label": "Stupovi",
      "kind": "single",
      "options": [
        {"value": "plate", "label": "Na beton, s pločicom", "short_label": null, "badge": null, "note": null},
        {
          "value": "concrete",
          "label": "U zemlju (betoniram)",
          "short_label": "U zemlju · po upitu",
          "badge": "po upitu · po narudžbi",
          "note": "Stupovi za betoniranje su po upitu i rade se po narudžbi, 10–15 dana. Beton ne prodajemo, nabavljate ga sami."
        },
        {"value": "unknown", "label": "Ne znam", "short_label": null, "badge": null, "note": null}
      ],
      "default": "plate"
    },
    {
      "id": "color",
      "text": "Koja boja?",
      "label": "Boja",
      "kind": "single",
      "options": [
        {"value": "antracit", "label": "Antracit (RAL 7016)", "short_label": "Antracit", "badge": null, "note": null},
        {"value": "zelena", "label": "Zelena (RAL 6005)", "short_label": "Zelena", "badge": null, "note": null},
        {"value": "any", "label": "Svejedno", "short_label": null, "badge": null, "note": null}
      ],
      "default": null
    }
  ]
}
```

### `product_cards`

Products that fit (`docs/mockups/*/3-results.html`). For an OLX product (`source`
`"olx"`), `url` is always `https://martom.olx.ba/artikal/{slug}`, and the slug ends in
`listing_id`. A manual product (`source` `"manual"`, from `manual_products.toml`, #132)
has no OLX listing: `listing_id` and `url` are null and `manual_key` names its TOML entry;
show it without the OLX button, and never build an olx.ba link for it. `price` is the
price of the shown variant per `price_unit`; each variant has its own price.

```json
{
  "type": "product_cards",
  "cards": [
    {
      "source": "olx",
      "listing_id": 63996799,
      "manual_key": null,
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
    },
    {
      "source": "manual",
      "listing_id": null,
      "manual_key": "anchor_m8x70",
      "url": null,
      "title": "Anker M8×70",
      "subtitle": null,
      "why": "Za stup s pločicom na betonu.",
      "badge": null,
      "photo_url": null,
      "price": "0.80",
      "price_unit": "kom",
      "variants": []
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
upit"); `made_to_order: true` marks a "po narudžbi" line that Toni confirms, and `label`
holds its status text.

`total` is never a fake `"0.00"`; `unpriced_count` says how many lines are "na upit":

| Lines | `total` | `unpriced_count` | UI (copy lands in #74/#27) |
| --- | --- | --- | --- |
| All priced | sum of the lines | `0` | "Ukupno: 530,00 KM" (the `extra_lines` example) |
| Some "na upit" | sum of the priced lines | number of "na upit" lines | "Ukupno: 254,00 KM + 1 stavka na upit" (the roll fence example) |
| All "na upit" | `null` | number of lines | "Ukupno: cijena na upit"; adding to the cart and ordering still work |

The models check rules the JSON Schema cannot express: `line_total` is
`unit_price × quantity`, `price_on_request` is true exactly when a line has no prices,
`total` is the sum of the priced lines (or `null` when there are none), and
`unpriced_count` equals the number of `price_on_request` lines. The plural ("1 stavka", "2
stavke", "5 stavki") is the frontend copy's job. `web/src/api/events.test.ts`
checks the same rules on the fixtures.

#### Posts set in concrete: `extra_lines`

The clarify question "Gdje idu stupovi?" offers "U zemlju (betoniram)" (badge "po upitu ·
po narudžbi"). Martom doesn't stock posts without a base plate, so that answer gives **no
BOM calculation** (fence-rules.md): the BOM keeps its base-plate posts, and the reply's
`bom` event carries one entry in `extra_lines`:

```json
{
  "type": "bom",
  "title": "Popis materijala",
  "summary": "25 m · panel 153 cm · antracit",
  "lines": [
    {
      "item": "panel",
      "name": "Panel 153 × 250 cm",
      "size": "153 × 250 cm",
      "quantity": 10,
      "unit_price": "53.00",
      "line_total": "530.00",
      "price_on_request": false,
      "made_to_order": false,
      "label": null
    }
  ],
  "total": "530.00",
  "unpriced_count": 0,
  "extra_lines": [
    {"name": "Stupovi za betoniranje – po upitu", "note": "Rade se po narudžbi, 10–15 dana."}
  ],
  "notes": []
}
```

An extra line is not a BOM line: it has no `item`, no quantity and no price field, it is
not counted in `total` or `unpriced_count`, and its quantity can't be edited. The cart and
the order message (#20, #27) copy it as a no-price line so Toni follows it up. The server
sends it, not the browser, because the customer may answer in free text ("betoniram
stupove") without ever tapping the chip. The example above is shortened to one line.

The roll fence example, shortened: the 100 cm support T-post has no price yet ("cijena na
upit"). Prices are illustrative.

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
  "extra_lines": [],
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
