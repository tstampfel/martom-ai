# Fence rules for the materials list (BOM)

The single source of truth for how the advisor builds a fence materials list. Toni (Martom owner) confirmed these rules on 26 Nov 2026. The BOM calculators (M4-1, M4-2), the clarify node (M4-3), the cart and order message (M5-5, M5-6) and the evals (M4-5) follow this file. If code and this file disagree, this file wins; change it only with Toni's confirmation, in its own PR.

Two rules from `CLAUDE.md` apply to everything below:

- **Quantities come from plain Python with unit tests**, never from the model.
- **Prices come only from the synced OLX data at call time.** The KM amounts here are reference values from the day Toni confirmed the rules. Use them in test fixtures and evals, never as constants in runtime code.

## Panel fence (panelna ograda)

Panels come from OLX listing 63996799.

| Panel height | Post with base plate (stup s pločicom) | PVC clips per post | Clips per corner post |
| --- | --- | --- | --- |
| 83 cm | 85 cm | 3 | 6 |
| 103 cm | 105 cm | 3 | 6 |
| 123 cm | 125 cm | 3 | 6 |
| 153 cm | 155 cm | 4 | 8 |
| 203 cm | 205 cm | 5 | 10 |

- **Panel widths:** 200 cm and 250 cm.
- **Posts:** base-plate posts are stock items, priced from OLX, and the default recommendation.
- **PVC clips:** 1,00 KM each (reference price).
- **Anchors M8×80:** 4 per post, corner posts included. 1,30 KM each (reference price).

### Corners (kutovi)

- A corner uses **one** post, shared by the two sides, not an extra post.
- That corner post gets **double clips**: 6 / 8 / 10 instead of 3 / 4 / 5.
- Anchors stay at 4 per corner post.
- The clarify step asks how many corners the fence has. The default is 0 for a straight run, but the advisor asks whenever the layout is unclear (for example "oko dvorišta", around the yard).

### Concrete-in-soil posts (stupovi za betoniranje)

- Martom does **not** stock posts without a base plate; customers rarely ask for them.
- The advisor may offer them as **"po narudžbi"** (made to order): no price, shown as "cijena na upit" and clearly labelled.
- The WhatsApp order message flags every "po narudžbi" line so Toni confirms it.
- Base-plate posts remain the default recommendation.

### Gates (kapija)

- A gate is a separate product. Toni will send sizes and prices later.
- Until that data exists, the advisor:
  - says gates are ordered separately;
  - may add a line **"Kapija – cijena na upit"** to the cart and order so Toni sees the request;
  - never calculates or shows a gate price.

## Roll fence (ograda u rolni)

Toni confirmed the posts and spacing on 26 Nov 2026. Whether pletivo and chain link use the same posts and spacing isn't confirmed yet.

### Posts: two models

| Model | Listing | Profile | Heights | Mounting |
| --- | --- | --- | --- | --- |
| (a) T-post | OLX 59473597 | green plastified T 35 × 35 × 3,50 mm | 120 / 125 / 150 / 175 / 200 cm | into soil or set in concrete |
| (b) Post with foot plate (stup sa stopom) | OLX "STUP STUPOVI KOCI ZA STOPOM 40X40MM 60, 80, 100, 125 I 150CM" (listing ID to confirm) | 40 × 40 mm | 60 / 80 / 100 / 125 / 150 cm | anchored onto a concrete wall or base |

- Prices per height always come from the OLX sync ("from 10,00 KM" for the T-post is a reference only).
- The clarify step for a roll fence asks where the posts go: **into soil or concrete → (a)**, **on a concrete wall or base → (b)**.
- Listing (b) currently sits in the OLX category "Oprema/pribor za stolariju", not "Ograde". Toni has been asked to move it. Until that's confirmed, the sync picks it up through an explicit listing allowlist, never by syncing the whole joinery category (issue #45).

### Post spacing and count

- Posts every **2 m**.
- Posts per straight run = **ceil(length / 2) + 1**.
- Adjacent runs share the corner post, so each corner removes one post from the sum of the runs.

## Eurofence (for reference)

OLX listing 48752061.

| Height | Mesh |
| --- | --- |
| 60 cm | 10 × 50 |
| 80 cm | 10 × 50 |
| 125 cm | 76 × 63 |

There is **no 100 cm** Eurofence. When asked for one, the advisor says so and offers 80 cm or 125 cm.

## Reference values for tests and evals

| Case | Expected |
| --- | --- |
| 25 m straight fence, 153 cm panels, base-plate posts, OLX prices of 26 Nov 2026 | Total **905,10 KM** |
| The same fence with 2 corners | **+8 clips** (each corner post goes from 4 to 8) |
| Customer asks for posts to set in concrete | A "po narudžbi" post line with no price, flagged in the order message |
| Customer asks for a 100 cm Eurofence | The advisor says it doesn't exist and offers 80 cm or 125 cm |
| Roll fence, 25 m straight | **14 posts** |
| Roll fence, 10 m straight | **6 posts** |
| Roll fence, two 10 m sides with 1 corner | **11 posts** (6 + 6 − 1 shared corner post) |

Fixtures record the OLX prices they use next to the expected total, so a later price change on OLX never breaks the test silently and never leaks into runtime code.

## Still open (don't invent values)

- **Gate sizes and prices.** Toni sends them later; until then gates are "cijena na upit".
- **Roll fence, still missing** (blocks M4-2):
  - which post height goes with each fence height;
  - accessories (tension wire, tensioners, ties) and their quantities;
  - anchors per foot-plate post (model b);
  - the OLX listing ID of the foot-plate post, and whether Toni moved it to "Ograde";
  - whether pletivo and chain link use the same posts and 2 m spacing.
- **The 905,10 KM breakdown:** which panel width (200 or 250 cm) and which line items Toni used. Confirm with Toni before the M4-1 fixture is final.
