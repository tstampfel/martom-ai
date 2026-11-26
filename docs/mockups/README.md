# Mockups — Martom AI savjetnik

Design reference for the customer page (issues M5-1 to M5-6). Live, clickable canvas: https://claude.ai/artifact/PRhcz41JRzk4dGZvuAVNJP

## The canvas is the source of truth

The design canvas is the source of truth for design (Tomislav, 26 Nov 2026). The repo mockups are synced from it, never the other way round: change a screen on the canvas first, then copy the change here. Last synced from canvas version 26 (M5-2d).

| Canvas | Repo |
| --- | --- |
| Web1Start · Web2Clarify · Web3Results · Web4Order · Web5Done | `web/1-start.html` · `2-clarify.html` · `3-results.html` · `4-cart-order.html` · `5-sent.html` |
| Main · Clarify · Results · Order · Done | `mobile/1-start.html` · `2-clarify.html` · `3-results.html` · `4-cart-order.html` · `5-sent.html` |

When you sync, copy layout, colours, copy and interactions exactly, and change only these:

- Links between screens use the repo names (`2-clarify.html`, not `Web2Clarify.dc.html`).
- Images use `../assets/...` instead of the canvas's `/_blob/...` URLs.
- Keep the repo's head and script lines (the font link uses a plain `&`).
- Photos that Toni hasn't approved stay striped placeholders with a note. The web start hero shows OLX 63996799's photo on the canvas; the repo keeps a placeholder until Toni approves it (#64).

The `.html` files are the **source of each screen** from the design canvas. They use the canvas's own component runtime (`<x-dc>`, `{{ }}` holes, `<sc-for>`), so they won't render on their own in a browser: read them for layout, spacing, colours, copy and interactions, and build the real UI in React. Striped grey boxes are photo placeholders; real product photos come from the OLX listings.

## Screens

The example story on every screen: *"Treba mi 25 m ograde za dvorište, visina oko 1,5 m"* → panel fence 153 cm, antracit, base-plate posts (the default). On screen 2 the customer also answers 2 corners with sides 10 + 5 + 10 m (example answers, not defaults); screens 3–5 still show straight-run placeholder quantities until the BOM (M4-1) and the real screens (M5-x) replace them. Gates are out of scope (`docs/domain/fence-rules.md`).

| # | Web (`web/`, 1440 wide, fluid) | Mobile (`mobile/`, 390 × 844) | What it shows |
| --- | --- | --- | --- |
| 1 | `1-start.html` | `1-start.html` | Red curved hero, question box whose placeholder types an example, pauses, erases with backspace speed and types the next; white quick-start chips; "Kako savjetnik radi" card with a "primjer" badge whose three example steps appear one by one and restart (shown all at once with reduced motion); mobile header has a charcoal top border; hexagon badges (2 prodavaonice, 30 godina, 10.000+ artikala, OLX); web adds fence category tiles and "Kako radi" |
| 2 | `2-clarify.html` | `2-clarify.html` | The advisor's 3 questions as tappable chips: koliko ima kutova (0 · 1 · 2 · 3 · 4+ · Ne znam, plus "Ide okolo (zatvoreno)") with one length field per side, stupovi (base plate preselected, concrete "po narudžbi, 10–15 dana", Ne znam), boja; web adds the dark "Vaš projekt" panel (Dužina, Kutovi, Strane, …) that fills in as answers arrive. Rules: `docs/domain/fence-rules.md` |
| 3 | `3-results.html` | `3-results.html` | Recommendation text, product cards with variant picker (panel = recommended, Garden Light = cheaper), materials list with working +/− quantity steppers, live total, "Dodaj sve u korpu" |
| 4 | `4-cart-order.html` | `4-cart-order.html` | Own cart (korpa) and order form with the same fields as martomshop.ba; **one** red "Pošalji narudžbu" button that opens WhatsApp with the order pre-filled |
| 5 | `5-sent.html` | `5-sent.html` | Confirmation, what happens next, and the exact WhatsApp message that was sent (use it as the message format for M5-6) |

Non-fence questions ("brusilica 125") skip screens 2 and the materials list on 3: straight to product cards.

## Design tokens

| Token | Value | Use |
| --- | --- | --- |
| Red (brand) | `#D71222` | Hero, primary buttons, hexagon badges, accents. The only red: it matches the solid red in the logo |
| Charcoal | `#26292E` | Top announcement bar, secondary buttons, dark panels ("Vaš projekt", materials list on web), footer, quick-start chip text |
| Ink | `#16181D` | Body text |
| Muted text | `#4F5560` / `#3B4049` | Secondary text (both pass 4.5:1 on the page background) |
| Page background | `#F2F2F0` | |
| Card | `#FFFFFF` with `1px #E2E2DE` border, radius 14–20 px | |
| Strong border | `#D5D6D1` | Outlined icon buttons (phone button on mobile) |
| Subtle divider / chip hover | `#ECECE8` | Quick-start chip hover (background and border), cart row dividers on web. Not in `tokens.css` yet; add it with the React issue that builds the chip hover |
| Text on dark | `#C9CDD4` | Secondary text on charcoal (footer, "Kako savjetnik radi" card) |
| Text on brand | `#FFFFFF` | Text and icons on red and charcoal |
| Success | `#157A3A` (text/icons), `#DDF2E3` (tint) | Confirmation |
| Agent card | `#4A4F57` (border), `#5A5F68` ("primjer" badge border), `#34C46A` (step done), `#F5A623` (step working) | "Kako savjetnik radi" card on charcoal. Since canvas version 26 the start screens show only done steps, so no mockup uses `#F5A623` now |
| Headings | Outfit 700/800 | |
| Body | Inter 400–700 | |
| Hexagon | `clip-path: polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)` | Badges and step numbers (echoes the hex nut in the logo) |

Touch targets are at least 44 px. Pills and buttons are fully rounded.

### Colour rule: where red goes (Tomislav, 26 Nov 2026)

- Red (`--color-red`, `#D71222`) is only for the hero, buttons and accents on light backgrounds, and the logo.
- Never red text on charcoal: `#D71222` on `#26292E` is only 2.8:1. Labels on charcoal (the "Vaš projekt" and "Popis materijala" eyebrows) use text on dark, `#C9CDD4`, with no decorative red mark.

## Assets

- `assets/martom-logo.png` — logo on a transparent background, for light backgrounds.
- `assets/martom-logo-white.png` — white logo for dark backgrounds (footer).

Both were captured from martom.springmedia.hr at 4× size. Ask Toni for the original vector logo before launch.

## Prices on the mockups

Real OLX prices from 26 Nov 2026: panel 153 × 250 cm = 53 KM, obujmica 50 × 50 = 1,50 KM, PVC traka 19 cm × 35 m = 73 KM, Garden Light 150 cm = 7,50 KM/m. Post price and clamps per post are placeholders (`[cijena]`, `[broj po stupu: Toni]`) until Toni gives the BOM rules (M4-1).
