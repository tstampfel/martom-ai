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
| (b) Post with foot plate (stup sa stopom) | OLX 81645615, [listing](https://martom.olx.ba/artikal/stup-stupovi-koci-za-stopom-40x40mm-60-80-100-125-i-150cm-81645615) (category 876) | 40 × 40 mm | 60 / 80 / 100 / 125 / 150 cm | anchored onto a concrete wall or base |

- Prices per height always come from the OLX sync ("from 10,00 KM" for the T-post is a reference only).
- **Foot-plate post (b), reference prices** (26 Nov 2026): 60 cm → 10,50 · 80 cm → 11,90 · 100 cm → 14,00 · 125 cm → 16,50 · 150 cm → 21,00 KM. Colours: green and anthracite.
- **Anchors for (b):** the foot plate has 3 anchor holes, so the BOM assumes **3 anchors per post** until Toni confirms. The anchor type is still pending, so the anchor line stays "cijena na upit" with no price until then.
- The clarify step for a roll fence asks where the posts go: **into soil or concrete → (a)**, **on a concrete wall or base → (b)**.
- Listing (b) currently sits in the OLX category "Oprema/pribor za stolariju", not "Ograde". Toni has been asked to move it. Until that's confirmed, listing **81645615** is on an explicit sync allowlist, never picked up by syncing the whole joinery category (issue #45).

### Accessories

| Item | Listing | Details | Quantity |
| --- | --- | --- | --- |
| Tensioner (španer) | OLX 18129143, category "Biznis i Industrija > Poljoprivreda > Ostalo" | green or galvanized; reference price 1,65 KM | **3 × ceil(length / 25)**: 3 for every started 25 m |
| Tension wire (žica nosiva) | OLX 46677177, category "Ograde" | PVC-coated, green or anthracite, 100 m coils, 3,10 / 2,7 / 2,2 mm | **optional**: quantity rule pending |

- **Tensioners** are always in the BOM.
- **Tension wire is optional ("po želji").** The advisor offers it but never adds it by default. Until Toni gives the quantity rule (how many rows, which thickness), the BOM doesn't calculate a wire quantity.
- **Allowlist:** the tensioner listing is outside the fence categories, so it goes on the explicit sync allowlist next to 81645615 (issue #45). The tension wire is in "Ograde" and needs no allowlist entry.

### Post spacing and count

- The BOM **always** calculates with posts every **2 m** (Toni's instruction).
- Up to 3 m is possible depending on the terrain. The advisor may mention it as a note, "razmak do 3 m ovisno o terenu – potvrdite s Martomom", but **never uses 3 m in the BOM**, even when the customer asks. If the customer asks for wider spacing, the BOM stays at 2 m and the note goes into the order message for Toni to confirm.
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
| Roll fence, customer asks for 3 m spacing | BOM still at 2 m, plus the "razmak do 3 m" note in the answer and in the order message |
| Roll fence, 25 m | **3 tensioners** |
| Roll fence, 30 m | **6 tensioners** (two started 25 m stretches) |
| Roll fence, any length | Tension wire offered as "po želji", **not** in the BOM by default |

### The 905,10 KM example, line by line

Toni confirmed the breakdown. The prices were checked against OLX listing 63996799 on 26 Nov 2026 and all match.

| Line | Qty | Unit price | Total |
| --- | --- | --- | --- |
| Panel 153 × 250 cm | 10 | 53,00 KM | 530,00 KM |
| Post 155 cm with base plate | 11 | 24,90 KM | 273,90 KM |
| PVC clip | 44 (11 × 4) | 1,00 KM | 44,00 KM |
| Anchor M8×80 | 44 (11 × 4) | 1,30 KM | 57,20 KM |
| **Total** | | | **905,10 KM** |

Where the prices live: posts, clips and anchors aren't separate OLX listings. Their prices are in the free-text description of the panel listing 63996799, next to the panel prices. Extraction (M2-1) must read them from there.

Fixtures record the OLX prices they use next to the expected total, so a later price change on OLX never breaks the test silently and never leaks into runtime code.

## Still open (don't invent values)

- **Gate sizes and prices.** Toni sends them later; until then gates are "cijena na upit".
- **Roll fence, still missing** (blocks M4-2):
  - which post height goes with each fence height;
  - tension wire quantity rule (rows, thickness), and whether ties are needed;
  - with corners, whether the tensioner rule uses the total length or each side;
  - anchors for the foot-plate post (model b): the anchor type, and confirmation of the assumed 3 per post;
  - whether Toni moved the foot-plate post (81645615) to "Ograde";
  - whether pletivo and chain link use the same posts and 2 m spacing.
