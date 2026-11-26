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
- **Installation:** Martom installs fences **only on an existing concrete wall**. The price is **always on request** (it depends on the location). The advisor may mention it but never quotes a price, and it is never a BOM or cart line.
- **Availability:** always show **"dostupno"** for stock products (Toni's decision). Never write "dostupnost potvrđujemo" or similar. "Po narudžbi" lines show their lead time instead.

### Gates (kapija): out of scope

- Gates are **out of scope** for now (Toni, 26 Nov 2026). The BOM has **no gate line** and no gate input, and nothing subtracts a gate opening from the length.
- When a customer asks about a gate, the advisor says **"Za kapije se javite direktno Martomu: 036 651 427"** (Martom's public shop number).

## Every BOM (all fence types)

- Every BOM shows the note **"Ponuda je informativnog karaktera – pribor možete izbaciti ako ga već imate."**
- Accessories are separate line items the customer can remove: tensioners, tension wire, wire holders, support T-posts (braces), PVC clips and anchors. Removing one never changes the other lines.

## Welded roll fence (varena ograda u roli)

Everything in this section is for the **welded** roll fence only, as confirmed by Toni on 26 Nov 2026. **Chain link (pletivo, baklava) uses different rules and posts, still pending from Toni.** The advisor must not apply these rules to chain link. Until Toni gives them, a chain-link request gets no BOM: the advisor says so and offers to contact Martom.

### Rolls

- A roll is **25 m** long.
- Rolls per fence = **ceil(total length / 25)**.
- Nothing comes with a roll. Posts, tensioners, wire and anchors are all sold separately and are separate BOM lines.

### Posts: two models

| Model | Listing | Profile | Heights | Mounting |
| --- | --- | --- | --- | --- |
| (a) T-post | OLX 59473597 | green plastified T 35 × 35 × 3,50 mm | 120 / 125 / 150 / 175 / 200 cm | into soil or set in concrete |
| (b) Post with foot plate (stup sa stopom) | OLX 81645615, [listing](https://martom.olx.ba/artikal/stup-stupovi-koci-za-stopom-40x40mm-60-80-100-125-i-150cm-81645615) (category 876) | 40 × 40 mm | 60 / 80 / 100 / 125 / 150 cm | anchored onto a concrete wall or base |

- Prices per height always come from the OLX sync ("from 10,00 KM" for the T-post is a reference only).
- The clarify step asks where the posts go: **into soil or concrete → (a)**, **on a concrete wall or base → (b)**.
- **T-post (a) height:** 30–50 cm of the post goes into the ground, with no fixed depth. **Toni's rule:** the T-post is the smallest available height ≥ roll height + 30 cm. Toni confirmed the 80 cm → 120 cm pairing; the other rows are derived from his rule.

  | Roll height | T-post | Status |
  | --- | --- | --- |
  | 60 cm | 120 cm | derived from Toni's rule |
  | 80 cm | 120 cm | **confirmed** |
  | 100 cm | 150 cm | derived from Toni's rule |
  | 125 cm | 175 cm | derived from Toni's rule |
  | 150 cm | 200 cm | derived from Toni's rule |

- **Foot-plate post (b) height = fence (roll) height** (Toni): 60 → 60, 80 → 80, 100 → 100, 125 → 125, 150 → 150 cm. Reference prices (26 Nov 2026): 60 cm → 10,50 · 80 cm → 11,90 · 100 cm → 14,00 · 125 cm → 16,50 · 150 cm → 21,00 KM. Colours: green and anthracite.
- **Anchors for (b):** **3 × M8×70 per post** (Toni confirmed). These are not the panel fence's M8×80 anchors. M8×70 has **no OLX listing**, so it comes from the `manual_products` config (see below), price 0,80 KM.
- **Allowlist:** listing (b) sits in "Oprema/pribor za stolariju", not "Ograde". Toni will try to move it (and the tensioner 18129143) to "Ograde". Until the sync actually sees a listing in "Ograde", **81645615** stays on the explicit sync allowlist (issue #45). The sync never syncs the whole joinery category to get one listing, and it dedupes, so a moved listing is never stored twice.

### Post spacing and count

- The BOM **always** calculates with posts every **2 m** (Toni's instruction).
- Up to 3 m is possible depending on the terrain. The advisor may mention it as a note, "razmak do 3 m ovisno o terenu – potvrdite s Martomom", but **never uses 3 m in the BOM**, even when the customer asks. If the customer asks for wider spacing, the BOM stays at 2 m and the note goes into the order message for Toni to confirm.
- Posts per straight run (side) = **ceil(side / 2) + 1**.
- Adjacent sides share the corner post, so each corner removes one post from the sum of the sides.

### Strainer and corner posts (kutni / zatezni stupovi)

- A strainer post stands at **every corner** and **every 25 m** along a side (Toni).
- A strainer **replaces** a line post; it is **not** an extra post (Toni). So the post count above doesn't change: a 30 m straight run has **16 posts**.
- Strainer posts are the **same listing and price** as line posts (same model and height).
- **Strainers per side** (not counting the side's two ends) = ceil(side / 25) − 1. Corner posts are strainers too.

### Bracing: support T-posts (potporni T-stupovi)

- **Each strainer and each corner post gets 2 support T-posts** (OLX 59473597), **the same height as the post they brace** (Toni). This applies to T-post fences and to foot-plate fences (example: foot-plate post 125 cm → 2 × T-post 125 cm).
- Support T-posts = **2 × (corners + Σ strainers per side)**.
- **End posts** (the two free ends of an open fence): whether they get braces is **pending from Toni**. The BOM adds none for them until then.
- **Foot-plate fences at 60, 80 and 100 cm:** T-posts exist only from 120 cm, so a brace of "the same height" doesn't exist. **Pending Toni**: until he answers, the brace line for those heights shows no size and no price ("cijena na upit").
- Braces are accessories: separate, removable lines.

### Accessories

| Item | Listing | Details | Quantity |
| --- | --- | --- | --- |
| Tensioner (španer) | OLX 18129143, category "Biznis i Industrija > Poljoprivreda > Ostalo" (allowlist) | green, galvanized or anthracite; reference price 1,65 KM (all prices from the sync) | **per side: 3 × ceil(side / 25)** |
| Tension wire (žica nosiva) | OLX 46677177, category "Ograde" | PVC-coated, green or anthracite, 100 m coils, **2,70 mm**; reference price 18,00 KM per 100 m coil | **optional**: coils = **ceil(rows × total length / 100)** |
| Wire holder (nosač nosive žice) | OLX 48475613, category "Ostalo" (allowlist) | foot-plate posts only | **assumption, pending Toni:** 1 per wire row per foot-plate post |
| Anchor M8×70 | no OLX listing; `manual_products` | 0,80 KM (Toni, 26 Nov 2026) | 3 per foot-plate post |

- **Tensioners are counted per side** (Toni): each side gets 3 per started 25 m. At a corner, each of the two sides has its own tensioner, which is already in that side's count. Example: 10 m + 10 m with 1 corner → 3 + 3 = **6**.
- **Tension wire is optional ("po želji").** The advisor offers it; it is in the BOM only when the customer wants it. The customer doesn't choose the rows; they follow the fence height (Toni): **≤ 100 cm → 2 rows, > 100 cm → 3 rows**.
- **Wire holders** are used only on foot-plate posts, and only when tension wire is in the BOM. T-posts need no holder: the wire is tied through the post's holes, and the tying wire isn't calculated.
- **Allowlist:** the tensioner **18129143** and the wire holder **48475613** are outside the fence categories, so they are on the explicit sync allowlist with 81645615 (issue #45), until the sync sees them in "Ograde".

### Products without an OLX listing (`manual_products`)

- A product Toni sells that has no OLX listing goes into a small, reviewed `manual_products` config file, never into code as a constant. Each entry has: name, price, source (e.g. "Toni 26.11.2026") and a `last_confirmed` date.
- First entry: **Anchor M8×70**, 0,80 KM, source "Toni 26.11.2026", last_confirmed 2026-11-26.
- The BOM and the cart treat a manual product like any other product (price, quantity, removable line).
- An entry whose `last_confirmed` is **more than 90 days old** raises an item in Toni's review queue (`review_issue`), so he reconfirms the price.

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
| Roll fence, 30 m straight | **16 posts** (the strainer at 25 m replaces a line post), **2 support T-posts** (1 strainer) |
| Roll fence, two 10 m sides with 1 corner | **2 support T-posts** (the corner post), none for the ends (end bracing pending) |
| Foot-plate fence 125 cm, 1 corner | 2 × **T-post 125 cm** as braces |
| Roll fence, customer asks for 3 m spacing | BOM still at 2 m, plus the "razmak do 3 m" note in the answer and in the order message |
| Roll fence, 25 m | **3 tensioners**, **1 roll** |
| Roll fence, 30 m straight | **6 tensioners** (two started 25 m stretches), **2 rolls** |
| Roll fence, 10 m + 10 m with 1 corner | **6 tensioners** (3 per side) |
| Roll fence, customer doesn't want tension wire | **no** wire line, no wire holders |
| Roll fence 125 cm, 25 m, with tension wire | 3 rows → **1 coil** 2,70 mm (18,00 KM reference) |
| Roll fence 150 cm, 40 m, with tension wire | 3 rows → **2 coils** 2,70 mm |
| Roll fence 80 cm, 25 m, with tension wire | 2 rows → **1 coil** |
| Roll fence, 80 cm roll, T-posts | **120 cm** T-posts |
| Roll fence, 100 cm roll, T-posts | **150 cm** T-posts (derived from Toni's ≥ roll + 30 cm rule) |
| Foot-plate fence 100 cm | **100 cm** foot-plate posts |
| Foot-plate fence, 10 posts | **30 × M8×70** at 0,80 KM (from `manual_products`) |
| Foot-plate fence, 10 posts, with tension wire, 125 cm | **30 wire holders** (3 rows × 10 posts; quantity rule pending Toni, mark the test as such) |
| T-post fence with tension wire | **no** wire holders |
| `manual_products` entry with last_confirmed older than 90 days | a `review_issue` for Toni |
| Customer asks for a chain-link / pletivo fence | **no BOM** with welded-roll rules; the advisor says the rules are pending and offers to contact Martom |
| Customer asks about a gate | no gate line; the advisor says "Za kapije se javite direktno Martomu: 036 651 427" |
| Customer asks about installation | mentioned only for an existing concrete wall, price on request, never a quoted price |
| Any BOM | shows "Ponuda je informativnog karaktera – pribor možete izbaciti ako ga već imate."; every accessory line can be removed |
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

- **Gates:** out of scope until Toni says otherwise.
- **Concrete-in-soil panel posts** for 83, 123, 153 and 203 cm panels: post size and price (Toni's answer was unclear; keep "cijena na upit").
- **Chain link (pletivo, baklava):** all rules and posts.
- **Welded roll fence, still pending** (the BOM leaves these out or shows "cijena na upit"; they don't block M4-2):
  - whether the two free end posts get support T-posts;
  - braces for foot-plate fences at 60, 80 and 100 cm (no T-post that short);
  - wire holders: confirm 1 per wire row per foot-plate post.
