# Fence rules for the materials list (BOM)

The single source of truth for how the advisor builds a fence materials list. Toni (Martom owner) confirmed these rules on 26 Nov 2026; gates, installation and anchors were updated on 28 Nov 2026 (#171). The BOM calculators (M4-1, M4-2), the clarify node (M4-3), the cart and order message (M5-5, M5-6) and the evals (M4-5) follow this file. If code and this file disagree, this file wins; change it only with Toni's confirmation, in its own PR.

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
- **Anchors: tiplo anker M8×80** ("Tiplo anker FI 10 / M8×80", Toni, 26 Nov 2026 14:29): a sleeve of ø10 mm with an M8 bolt inside, for the base plate (120 × 80 mm, 4 holes). **4 per post**, corner posts included. 1,30 KM each (reference price). Drill bit: **ø10 mm, SDS** (Toni, 28 Nov 2026; this replaces the 26 Nov 14:32 "8 mm, not 10 mm"), so installation needs an SDS hammer drill (relevant for suggested tools, #59).
- **Anchors are "prateći materijal"**: sold separately, never included with the posts (Toni, 28 Nov 2026). They are always their own BOM line when there are posts. Asked "dolaze li ankeri uz stupove?", the advisor answers **"Ne, ankeri se kupuju posebno; za vaš popis smo ih već uračunali."**, the second half only when this chat's materials list has an anchor line, otherwise just **"Ne, ankeri se kupuju posebno."**
- **Drilling answers always name the anchor type with its bit** (Toni, 28 Nov 2026): tiplo anker M8×80 → ø10 mm SDS, anker vijak M8×70 / M8×80 → ø8 mm; never a bare "8 mm" or "10 mm". The grounding check fails a sentence with a bit size and no anchor type.
- **No braces / support posts on panel fences at all**, end posts included (Toni, 26 Nov 2026 14:29).

### Corners (kutovi)

- A corner uses **one** post, shared by the two sides, not an extra post.
- That corner post gets **double clips**: 6 / 8 / 10 instead of 3 / 4 / 5.
- Anchors stay at 4 per corner post.
- The clarify step asks how many corners the fence has. The default is 0 for a straight run, but the advisor asks whenever the layout is unclear (for example "oko dvorišta", around the yard).

### Concrete-in-soil posts (stupovi za betoniranje): not in the BOM

- **The BOM calculator never adds concrete-in-soil posts** (Toni, 26 Nov 2026 14:52: nobody orders them). The panel BOM always uses base-plate posts.
- Martom does **not** stock posts without a base plate. When a customer asks for them, the advisor says: **"Stupovi za betoniranje – po upitu (rade se po narudžbi, 10–15 dana)"**, and adds a **no-price note** to the order so Toni follows it up. The wording and the order note are built in the advisor and order issues (#20, #27), not in the BOM.
- **Reference only, not used by any code:** Toni's size mapping. A concrete post is **one size up** from the base-plate post and is **priced like the size below**, i.e. the base-plate post for the same panel height. Prices would come from the OLX sync, never from this table.

  | Panel height | Base-plate post | Concrete post (no plate) | Priced like |
  | --- | --- | --- | --- |
  | 83 cm | 85 cm | 105 cm | base-plate post 85 cm |
  | 103 cm | 105 cm | 125 cm | base-plate post 105 cm |
  | 123 cm | 125 cm | 155 cm | base-plate post 125 cm |
  | 153 cm | 155 cm | 205 cm | base-plate post 155 cm |
  | 203 cm | 205 cm | none | – |

- Base-plate posts remain the only posts in a panel BOM.

### Concrete, installation and availability

- **Martom does not sell concrete and does no construction work.** The advisor never suggests buying concrete from Martom and never adds concrete to the BOM. It may say the customer needs their own concrete for concrete-in-soil posts.
- **Installation:** Martom offers installation **on a finished concrete wall**; the price is **always by offer** (it depends on the location), and **delivery is free with Martom's installation** (Tomislav, 28 Nov 2026, #171). The advisor answers **"Nudimo montažu na gotov betonski zid, cijena po ponudi; uz našu montažu dostava je besplatna. Javite se: 036 651 427 ili info@martom-bekaert.com"**. It never quotes a price, and installation is never a BOM or cart line.
- **Availability:** always show **"dostupno"** for stock products (Toni's decision). Never write "dostupnost potvrđujemo" or similar. No BOM line is "po narudžbi"; concrete-in-soil posts are answered by the advisor as "po upitu" (see "Concrete-in-soil posts").

### Gates (kapija): made to order

- Martom makes gates **to order** (Tomislav, 28 Nov 2026, from Toni's 3D-panel Q&A, #171; this replaces Toni's 26 Nov "out of scope"). The BOM still has **no gate line** and no gate input, and nothing subtracts a gate opening from the length.
- When a customer asks about a gate, the advisor says **"Kapije radimo po narudžbi, javite se za ponudu: 036 651 427 ili info@martom-bekaert.com"** (Martom's public shop number and e-mail). It never gives a gate price; the grounding check fails a price in a sentence about a gate.

## Every BOM (all fence types)

- Every BOM shows one note line: **"Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. Dostava se potvrđuje."** (Tomislav merged Toni's sentence with the existing delivery note, 26 Nov 2026).
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
- **Anchors for (b), foot-plate posts 81645615 (3 holes):** **anker vijak M8×70 or M8×80** (a bolt anchor, no sleeve), **3 per post** for the foot plate's 3 holes; drill bit **ø8 mm** (Toni, 28 Nov 2026). The default and the BOM line stay **M8×70**; the advisor may mention M8×80 as an alternative. These are not the panel fence's tiplo anker M8×80. M8×70 has **no OLX listing**, so it comes from the `manual_products` config (see below), price 0,80 KM (re-confirmed by Toni 26 Nov 2026 14:29). Anchors are prateći materijal, sold separately (see "Panel fence").
- T-posts (a) need no anchors.
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
- Support T-posts = **2 × (corners + Σ strainers per side) + 1 × end posts** (end posts: 2 for an open fence, 0 for a closed loop).
- **End posts** (the two free ends of an open fence, where it starts and ends): **1 support T-post each**, the same height as the post (Toni, 26 Nov 2026 14:53). An open fence therefore gets **2 end-post braces**; a closed loop has no end posts and gets none. Corners and strainers stay at 2.
- End-post braces follow the **same size rule** as corner and strainer braces, including the 100 cm support T-post below for 60, 80 and 100 cm foot-plate fences (Tomislav, 26 Nov 2026).
- **Foot-plate fences at 60, 80 and 100 cm:** the braces are **support T-posts 100 cm** (Toni, 26 Nov 2026 14:29), 2 per strainer or corner post and 1 per end post as usual, reference price 11,50 KM. The 100 cm variant **isn't on OLX yet**; Toni will add it to the T-post listing 59473597. Until the sync sees a 100 cm variant there, the line reads **"potporni stup 100 cm – cijena na upit"**, with the count and the size but **no price**. Never take the 11,50 KM from this document: prices come only from the sync.
- Braces are accessories: separate, removable lines.

### Accessories

| Item | Listing | Details | Quantity |
| --- | --- | --- | --- |
| Tensioner (španer) | OLX 18129143, category "Biznis i Industrija > Poljoprivreda > Ostalo" (allowlist) | green, galvanized or anthracite; reference price 1,65 KM (all prices from the sync) | **per side: 3 × ceil(side / 25)** |
| Tension wire (žica nosiva) | OLX 46677177, category "Ograde" | PVC-coated, green or anthracite, 100 m coils, **2,70 mm**; reference price 18,00 KM per 100 m coil | **optional**: coils = **ceil(rows × total length / 100)** |
| Wire holder (nosač nosive žice) | OLX 48475613, category "Ostalo" (allowlist) | foot-plate posts only | **assumption, pending Toni:** 1 per wire row per foot-plate post |
| Anker vijak M8×70 (anchor) | no OLX listing; `manual_products` | 0,80 KM (Toni, 26 Nov 2026) | 3 per foot-plate post |

- **Tensioners are counted per side** (Toni): each side gets 3 per started 25 m. At a corner, each of the two sides has its own tensioner, which is already in that side's count. Example: 10 m + 10 m with 1 corner → 3 + 3 = **6**.
- **Tension wire is optional ("po želji")** and **left out of the BOM by default** (Tomislav, 26 Nov 2026: many customers have their own). The BOM returns it as a **suggested add-on** with the quantity already calculated: rows by fence height (Toni: **≤ 100 cm → 2 rows, > 100 cm → 3 rows**), coils, and for foot-plate fences the wire holders. The UI shows it under the BOM with a one-tap **"Dodaj nosivu žicu"** button that adds the wire (and holders) as normal, removable lines.
- **Wire holders** are used only on foot-plate posts, and only when tension wire is added; they are part of the wire add-on. T-posts need no holder: the wire is tied through the post's holes, and the tying wire isn't calculated.
- **Allowlist:** the tensioner **18129143** and the wire holder **48475613** are outside the fence categories, so they are on the explicit sync allowlist with 81645615 (issue #45), until the sync sees them in "Ograde".

### Products without an OLX listing (`manual_products`)

- A product Toni sells that has no OLX listing goes into a small, reviewed `manual_products` config file, never into code as a constant. Each entry has: name, price, source (e.g. "Toni 26.11.2026") and a `last_confirmed` date.
- First entry: **Anker vijak M8×70**, 0,80 KM, source "Toni 26.11.2026", last_confirmed 2026-11-26.
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
| Panel fence, customer asks for posts to set in concrete | **No concrete post line**: the BOM keeps base-plate posts; the advisor says "Stupovi za betoniranje – po upitu (rade se po narudžbi, 10–15 dana)" and adds a no-price note to the order |
| Customer asks whether Martom sells concrete or builds the base | The advisor says Martom doesn't sell concrete or do construction work; it never adds concrete to the BOM |
| Any stock product | Shown as "dostupno"; never "dostupnost potvrđujemo" |
| Customer asks for a 100 cm Eurofence | The advisor says it doesn't exist and offers 80 cm or 125 cm |
| Roll fence, 25 m straight | **14 posts** |
| Roll fence, 10 m straight | **6 posts** |
| Roll fence, two 10 m sides with 1 corner | **11 posts** (6 + 6 − 1 shared corner post) |
| Roll fence, 30 m straight | **16 posts** (the strainer at 25 m replaces a line post), **4 support T-posts** (2 for the strainer, 1 per end post) |
| Roll fence, 25 m straight | **2 support T-posts** (1 per end post) |
| Roll fence, two 10 m sides with 1 corner | **4 support T-posts** (2 for the corner post, 1 per end post) |
| Roll fence, closed loop of 4 × 10 m | **8 support T-posts** (4 corners × 2, no end posts) |
| Foot-plate fence 125 cm, 1 corner, open | 4 × **T-post 125 cm** as braces (2 corner + 2 end) |
| Roll fence, customer asks for 3 m spacing | BOM still at 2 m, plus the "razmak do 3 m" note in the answer and in the order message |
| Roll fence, 25 m | **3 tensioners**, **1 roll** |
| Roll fence, 30 m straight | **6 tensioners** (two started 25 m stretches), **2 rolls** |
| Roll fence, 10 m + 10 m with 1 corner | **6 tensioners** (3 per side) |
| Any welded roll BOM | **no** wire line and no wire holders in the BOM; a suggested "Dodaj nosivu žicu" add-on with rows, coils (and holders on foot-plate posts) already calculated |
| Roll fence 125 cm, 25 m, wire add-on | 3 rows → **1 coil** 2,70 mm (18,00 KM reference) |
| Roll fence 150 cm, 40 m, wire add-on | 3 rows → **2 coils** 2,70 mm |
| Roll fence 80 cm, 25 m, wire add-on | 2 rows → **1 coil** |
| Roll fence, 80 cm roll, T-posts | **120 cm** T-posts |
| Roll fence, 100 cm roll, T-posts | **150 cm** T-posts (derived from Toni's ≥ roll + 30 cm rule) |
| Foot-plate fence 100 cm | **100 cm** foot-plate posts |
| Foot-plate fence, 10 posts | **30 × M8×70** at 0,80 KM (from `manual_products`) |
| Foot-plate fence, 10 posts, wire add-on, 125 cm | **30 wire holders** (3 rows × 10 posts; quantity rule pending Toni, mark the test as such) |
| T-post fence with tension wire | **no** wire holders |
| `manual_products` entry with last_confirmed older than 90 days | a `review_issue` for Toni |
| Customer asks for a chain-link / pletivo fence | **no BOM** with welded-roll rules; the advisor says the rules are pending and offers to contact Martom |
| Customer asks about a gate | no gate line; the advisor says "Kapije radimo po narudžbi, javite se za ponudu: 036 651 427 ili info@martom-bekaert.com", never a price |
| Customer asks about installation | "Nudimo montažu na gotov betonski zid, cijena po ponudi; uz našu montažu dostava je besplatna", with the phone and e-mail, never a quoted price |
| Customer asks whether anchors come with the posts | "Ne, ankeri se kupuju posebno."; plus "za vaš popis smo ih već uračunali" only when the chat's materials list has anchors |
| Customer asks which drill bit, panel fence | "tiplo anker M8×80 – svrdlo ø10 mm SDS" |
| Customer asks which drill bit, roll fence with foot-plate posts | "anker vijak M8×70 (može i M8×80) – svrdlo ø8 mm" |
| Any BOM | shows "Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. Dostava se potvrđuje."; every accessory line can be removed |
| Foot-plate fence 80 cm, 1 corner, open | brace line "potporni stup 100 cm – cijena na upit", 4 pieces (2 corner + 2 end), size 100 cm, no price (until the sync sees the 100 cm T-post variant) |
| Panel fence, any layout | **no** braces / support posts, end posts included |
| Panel fence, no width given | **250 cm** panels |
| Panel fence, 25 m straight, 153 cm | End posts get **4** clips each, like line posts (44 clips for 11 posts) |

### The 905,10 KM example, line by line

Toni confirmed the breakdown. The prices were checked against OLX listing 63996799 on 26 Nov 2026 and all match.

| Line | Qty | Unit price | Total |
| --- | --- | --- | --- |
| Panel 153 × 250 cm | 10 | 53,00 KM | 530,00 KM |
| Post 155 cm with base plate | 11 | 24,90 KM | 273,90 KM |
| PVC clip | 44 (11 × 4) | 1,00 KM | 44,00 KM |
| Tiplo anker M8×80 | 44 (11 × 4) | 1,30 KM | 57,20 KM |
| **Total** | | | **905,10 KM** |

Where the prices live: posts, clips and anchors aren't separate OLX listings. Their prices are in the free-text description of the panel listing 63996799, next to the panel prices. Extraction (M2-1) must read them from there.

Fixtures record the OLX prices they use next to the expected total, so a later price change on OLX never breaks the test silently and never leaks into runtime code.

## Still open (don't invent values)

- **Chain link (pletivo, baklava):** all rules and posts.
- **Welded roll fence, still pending** (the BOM keeps the assumption; it doesn't block M4-2):
  - wire holders: confirm 1 per wire row per foot-plate post (still unanswered at 14:29; keep the assumption).

Answered on 26 Nov 2026 (14:52–14:53): concrete-in-soil panel posts (dropped from the BOM, mapping kept as reference) and welded roll end posts (1 support T-post each). Answered on 28 Nov 2026: gates (made to order), installation (by offer, free delivery with it) and the anchors and drill bits for both post types.
