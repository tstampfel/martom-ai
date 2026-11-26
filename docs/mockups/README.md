# Mockups — Martom AI savjetnik

Design reference for the customer page (issues M5-1 to M5-6). Live, clickable canvas: https://claude.ai/artifact/PRhcz41JRzk4dGZvuAVNJP

The `.html` files are the **source of each screen** from the design canvas. They use the canvas's own component runtime (`<x-dc>`, `{{ }}` holes, `<sc-for>`), so they won't render on their own in a browser: read them for layout, spacing, colours, copy and interactions, and build the real UI in React. Striped grey boxes are photo placeholders; real product photos come from the OLX listings.

## Screens

The example story on every screen: *"Treba mi 25 m ograde za dvorište, visina oko 1,5 m"* → panel fence 153 cm, antracit, posts in the ground, no gate.

| # | Web (`web/`, 1440 wide, fluid) | Mobile (`mobile/`, 390 × 844) | What it shows |
| --- | --- | --- | --- |
| 1 | `1-start.html` | `1-start.html` | Red curved hero, question box whose placeholder types an example, pauses, erases with backspace speed and types the next; quick-start chips; "Savjetnik radi" live agent card; hexagon badges (2 prodavaonice, 30 godina, 10.000+ artikala, OLX); web adds fence category tiles and "Kako radi" |
| 2 | `2-clarify.html` | `2-clarify.html` | The advisor's 3 questions as tappable chips (boja, stupovi, kapija); web adds the dark "Vaš projekt" panel that fills in as answers arrive |
| 3 | `3-results.html` | `3-results.html` | Recommendation text, product cards with variant picker (panel = recommended, Garden Light = cheaper), materials list with working +/− quantity steppers, live total, "Dodaj sve u korpu" |
| 4 | `4-cart-order.html` | `4-cart-order.html` | Own cart (korpa) and order form with the same fields as martomshop.ba; **one** red "Pošalji narudžbu" button that opens WhatsApp with the order pre-filled |
| 5 | `5-sent.html` | `5-sent.html` | Confirmation, what happens next, and the exact WhatsApp message that was sent (use it as the message format for M5-6) |

Non-fence questions ("brusilica 125") skip screens 2 and the materials list on 3: straight to product cards.

## Design tokens

| Token | Value | Use |
| --- | --- | --- |
| Red (brand) | `#E30613` | Hero, primary buttons, top strip, hexagon badges, accents |
| Charcoal | `#26292E` | Secondary buttons, dark panels ("Vaš projekt", materials list on web), footer |
| Ink | `#16181D` | Body text |
| Muted text | `#4F5560` / `#3B4049` | Secondary text (both pass 4.5:1 on the page background) |
| Page background | `#F2F2F0` | |
| Card | `#FFFFFF` with `1px #E2E2DE` border, radius 14–20 px | |
| Strong border | `#D5D6D1` | Outlined icon buttons (phone button on mobile) |
| Text on dark | `#C9CDD4` | Secondary text on charcoal (footer, "Savjetnik radi" card) |
| Text on brand | `#FFFFFF` | Text and icons on red and charcoal |
| Success | `#157A3A` (text/icons), `#DDF2E3` (tint) | Confirmation |
| Agent card | `#4A4F57` (border), `#5A5F68` (dashed border), `#34C46A` (step done), `#F5A623` (step working) | "Savjetnik radi" card on charcoal |
| Headings | Outfit 700/800 | |
| Body | Inter 400–700 | |
| Hexagon | `clip-path: polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)` | Badges and step numbers (echoes the hex nut in the logo) |

Touch targets are at least 44 px. Pills and buttons are fully rounded.

## Assets

- `assets/martom-logo.png` — logo on a transparent background, for light backgrounds.
- `assets/martom-logo-white.png` — white logo for dark backgrounds (footer).

Both were captured from martom.springmedia.hr at 4× size. Ask Toni for the original vector logo before launch.

## Prices on the mockups

Real OLX prices from 26 Nov 2026: panel 153 × 250 cm = 53 KM, obujmica 50 × 50 = 1,50 KM, PVC traka 19 cm × 35 m = 73 KM, Garden Light 150 cm = 7,50 KM/m. Post price and clamps per post are placeholders (`[cijena]`, `[broj po stupu: Toni]`) until Toni gives the BOM rules (M4-1).
