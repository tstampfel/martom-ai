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

- **Panel widths:** 200 cm and 250 cm. The default is **250 cm** (fewer posts, so cheaper). The advisor offers 200 cm only when the customer asks for it or when the length fits 200 cm panels better.
- **End posts:** an end post gets the same clips as a line post (3 / 4 / 5). Only corner posts get double clips.
- **Posts:** base-plate posts are stock items, priced from OLX, and the default recommendation.
- **PVC clips:** 1,00 KM each (reference price).
- **Anchors M8×80:** 4 per post, corner posts included. 1,30 KM each (reference price).

### Corners (kutovi)

- A corner uses **one** post, shared by the two sides, not an extra post.
- That corner post gets **double clips**: 6 / 8 / 10 instead of 3 / 4 / 5.
- Anchors stay at 4 per corner post.
- The clarify step asks how many corners the fence has. The default is 0 for a straight run, but the advisor asks whenever the layout is unclear (for example "oko dvorišta", around the yard).

### Concrete-in-soil posts (stupovi za betoniranje)

- Martom does **not** stock posts without a base plate; customers rarely ask for them. They are made to order, with a lead time of **10–15 days**.
- The BOM line is labelled **"po narudžbi, 10–15 dana"**.
- **Size and price (Toni, 26 Nov 2026):** the concrete post is **one size taller** than the base-plate post for the same panel height, and costs the **same as that base-plate post**. The price comes from the OLX sync at call time, never from code.

  | Panel height | Base-plate post | Concrete post (no plate) | Price | Status |
  | --- | --- | --- | --- | --- |
  | 103 cm | 105 cm | **125 cm** | = base-plate post 105 cm (19,00 KM reference, OLX) | **confirmed** |
  | 83, 123, 153, 203 cm | 85 / 125 / 155 / 205 cm | pending | "cijena na upit", no price | pending Toni |

- For the pending heights, **don't extrapolate** the "one size taller" rule: the line shows no post size and no price ("cijena na upit") until Toni confirms each height.
- Concrete posts get **no anchors** (there is no plate). Clips stay as for base-plate posts.
- The WhatsApp order message flags every "po narudžbi" line so Toni confirms it.
- Base-plate posts remain the default recommendation.

### Concrete, installation and availability

- **Martom does not sell concrete and does no construction work.** The advisor never suggests buying concrete from Martom and never adds concrete to the BOM. It may say the customer needs their own concrete for concrete-in-soil posts.
- **Installation:** Martom installs fences **only on an existing concrete wall**. The advisor may mention it, with the price on request. How to present it is pending Toni's confirmation, so for now it is a mention only, never a BOM or cart line.
- **Availability:** always show **"dostupno"** for stock products (Toni's decision). Never write "dostupnost potvrđujemo" or similar. "Po narudžbi" lines show their lead time instead.

### Gates (kapija)

- A gate is a separate product. Toni will send sizes and prices later.
- Until that data exists, the advisor:
  - says gates are ordered separately;
  - may add a line **"Kapija – cijena na upit"** to the cart and order so Toni sees the request;
  - never calculates or shows a gate price.

## Roll fence (ograda u rolni)

Toni confirmed the posts and spacing on 26 Nov 2026. Whether pletivo and chain link use the same posts and spacing isn't confirmed yet.

### Rolls

- A roll is **25 m** long.
- Rolls per fence = **ceil(length / 25)**.
- Nothing comes with a roll. Posts, tensioners, wire and anchors are all sold separately and are separate BOM lines.

### Posts: two models

| Model | Listing | Profile | Heights | Mounting |
| --- | --- | --- | --- | --- |
| (a) T-post | OLX 59473597 | green plastified T 35 × 35 × 3,50 mm | 120 / 125 / 150 / 175 / 200 cm | into soil or set in concrete |
| (b) Post with foot plate (stup sa stopom) | OLX 81645615, [listing](https://martom.olx.ba/artikal/stup-stupovi-koci-za-stopom-40x40mm-60-80-100-125-i-150cm-81645615) (category 876) | 40 × 40 mm | 60 / 80 / 100 / 125 / 150 cm | anchored onto a concrete wall or base |

- Prices per height always come from the OLX sync ("from 10,00 KM" for the T-post is a reference only).
- **T-post (a) height:** 30–50 cm of the post goes into the ground, with no fixed depth. **Toni's rule:** the T-post is the smallest available height ≥ roll height + 30 cm. Toni confirmed the 80 cm → 120 cm pairing; the other rows are derived from his rule.

  | Roll height | T-post | Status |
  | --- | --- | --- |
  | 60 cm | 120 cm | derived from Toni's rule |
  | 80 cm | 120 cm | **confirmed** |
  | 100 cm | 150 cm | derived from Toni's rule |
  | 125 cm | 175 cm | derived from Toni's rule |
  | 150 cm | 200 cm | derived from Toni's rule |
- **Foot-plate post (b), reference prices** (26 Nov 2026): 60 cm → 10,50 · 80 cm → 11,90 · 100 cm → 14,00 · 125 cm → 16,50 · 150 cm → 21,00 KM. Colours: green and anthracite.
- **Anchors for (b):** **3 anchors M8×70 per post** (Toni confirmed). These are not the panel fence's M8×80 anchors; keep them as separate products. No OLX listing for M8×70 has been found yet, so the anchor line carries the quantity with "cijena na upit" and no price until it exists.
- **Support T-posts (potporni T-stupovi):** foot-plate post fences are braced with support T-posts. How many and which height is pending from Toni, so the BOM adds no support-post line yet.
- The clarify step for a roll fence asks where the posts go: **into soil or concrete → (a)**, **on a concrete wall or base → (b)**.
- Listing (b) sits in the OLX category "Oprema/pribor za stolariju", not "Ograde". Toni will try to move it (and the tensioner 18129143) to "Ograde". Until the sync actually sees a listing in "Ograde", listing **81645615** stays on an explicit sync allowlist, never picked up by syncing the whole joinery category (issue #45). The sync dedupes, so a moved listing is never stored twice.

### Accessories

| Item | Listing | Details | Quantity |
| --- | --- | --- | --- |
| Tensioner (španer) | OLX 18129143, category "Biznis i Industrija > Poljoprivreda > Ostalo" | green, galvanized or anthracite; reference price 1,65 KM (the anthracite price comes from the sync) | **3 × ceil(length / 25)**: 3 for every started 25 m |
| Tension wire (žica nosiva) | OLX 46677177, category "Ograde" | PVC-coated, green or anthracite, 100 m coils; Toni's thickness for roll fences is **2,70 mm**; reference price 18,00 KM per 100 m coil | **optional**: coils = **ceil(rows × length / 100)**, rows 2 or 3 |

- **Tensioners** are always in the BOM.
- **Tension wire is optional ("po želji").** The advisor asks whether the customer wants it and, if so, **2 or 3 rows**. There is **no default**: without the customer's answer, the BOM has no wire line. With an answer, coils = ceil(rows × length / 100), 2,70 mm.
- **Allowlist:** the tensioner listing **18129143** is outside the fence categories, so it stays on the explicit sync allowlist next to 81645615 (issue #45) until the sync sees it in "Ograde". The tension wire is in "Ograde" and needs no allowlist entry.

### Post spacing and count

- The BOM **always** calculates with posts every **2 m** (Toni's instruction).
- Up to 3 m is possible depending on the terrain. The advisor may mention it as a note, "razmak do 3 m ovisno o terenu – potvrdite s Martomom", but **never uses 3 m in the BOM**, even when the customer asks. If the customer asks for wider spacing, the BOM stays at 2 m and the note goes into the order message for Toni to confirm.
- Posts per straight run = **ceil(length / 2) + 1**.
- Adjacent runs share the corner post, so each corner removes one post from the sum of the runs.

### Corner and strainer posts (kutni stupovi)

- A strainer post stands at **every corner** and **every 25 m** (Toni). Whether the two ends of a run count as strainer posts is pending; it doesn't change the count.
- Strainer posts have the **same height and the same price** as line posts. Whether they are the same OLX listing, and whether they need a brace, is pending from Toni.
- **Derived count, pending Toni (extra strainer post or replacing a line post):** a run longer than 25 m is split into 25 m sections plus the remainder. Each section counts ceil(section / 2) + 1 posts, and adjacent sections share their strainer post. Example: 30 m = 25 m + 5 m → 14 + 4 − 1 = **17 posts** (one more than ceil(30 / 2) + 1 = 16, because the strainer at 25 m doesn't fall on the 2 m grid). Runs up to 25 m are unchanged.

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
| Panel fence, 103 cm panels, customer asks for posts to set in concrete | **125 cm** concrete posts (no plate) at **19,00 KM** each (= base-plate post 105 cm, OLX reference 26 Nov 2026), line "po narudžbi, 10–15 dana", **no anchors**, flagged in the order message |
| Panel fence, 153 cm panels, posts to set in concrete | A "po narudžbi, 10–15 dana" post line with **no size and no price** ("cijena na upit"), no anchors, flagged in the order message |
| Customer asks whether Martom sells concrete or builds the base | The advisor says Martom doesn't sell concrete or do construction work; it never adds concrete to the BOM |
| Any stock product | Shown as "dostupno"; never "dostupnost potvrđujemo" |
| Customer asks for a 100 cm Eurofence | The advisor says it doesn't exist and offers 80 cm or 125 cm |
| Roll fence, 25 m straight | **14 posts** |
| Roll fence, 10 m straight | **6 posts** |
| Roll fence, two 10 m sides with 1 corner | **11 posts** (6 + 6 − 1 shared corner post) |
| Roll fence, customer asks for 3 m spacing | BOM still at 2 m, plus the "razmak do 3 m" note in the answer and in the order message |
| Roll fence, 25 m | **3 tensioners** |
| Roll fence, 30 m | **6 tensioners** (two started 25 m stretches) |
| Roll fence, any length, customer hasn't chosen wire rows | Tension wire offered as "po želji" (2 or 3 rows), **not** in the BOM |
| Roll fence, 25 m, 3 rows of tension wire | **1 coil** 2,70 mm (18,00 KM reference) |
| Roll fence, 40 m, 3 rows of tension wire | **2 coils** 2,70 mm |
| Roll fence, 25 m | **1 roll** |
| Roll fence, 30 m | **2 rolls** |
| Roll fence, 80 cm roll, T-posts | **120 cm** T-posts |
| Roll fence, 100 cm roll, T-posts | **150 cm** T-posts (derived from Toni's ≥ roll + 30 cm rule) |
| Roll fence, 30 m straight | 17 posts (derived strainer rule, pending Toni; mark the test as such) |
| Roll fence on foot-plate posts, 10 posts | **30 anchors M8×70**, no price ("cijena na upit") |
| Panel fence, no width given | **250 cm** panels |
| Panel fence, 25 m straight, 153 cm | End posts get **4** clips each, like line posts (44 clips for 11 posts) |

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

- **Gates:** sizes, prices and gate posts. Toni sends them separately; until then gates are "cijena na upit".
- **Concrete-in-soil panel posts** for 83, 123, 153 and 203 cm panels: post size and price.
- **Installation on an existing concrete wall:** how the advisor presents it.
- **Roll fence, still missing** (blocks M4-2):
  - strainer posts: an extra post at each 25 m mark, or replacing a line post (the derived count assumes extra);
  - strainer/corner posts: same listing as line posts or not, and whether they need a brace;
  - support T-posts for foot-plate fences: how many and which height;
  - with corners, whether the tensioner rule uses the total length or each side;
  - the OLX listing for the M8×70 anchors;
  - which foot-plate post (b) height goes with each roll height (not yet asked);
  - whether ties are needed for the tension wire (not yet asked);
  - whether pletivo and chain link use the same posts and 2 m spacing (not yet asked).
