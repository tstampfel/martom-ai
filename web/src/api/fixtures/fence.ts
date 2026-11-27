// A fence conversation on one thread: the customer asks for 25 m of yard
// fence, the advisor asks three clarify questions (2-clarify.html), the
// customer answers and gets products and a materials list (3-results.html).
// fenceBom answers the posts question with base-plate posts; fenceConcretePosts
// is the alternative second reply when the posts go in concrete.
// Listing 63996799 and its slug are recorded (tests/fixtures/olx). The panel
// fence BOM prices are the fence-rules.md reference prices; every other price
// (the roll fence, the cards' variants) is illustrative.

import type { BomLine, ChatEvent } from '../events'

const THREAD_ID = '3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10'
const NOTE = 'Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. Dostava se potvrđuje.'

/** A BOM line; without prices it is "na upit". */
function line(
  item: string,
  name: string,
  size: string | null,
  quantity: number,
  unit_price: string | null,
  line_total: string | null,
): BomLine {
  return {
    item,
    name,
    size,
    quantity,
    unit_price,
    line_total,
    price_on_request: unit_price === null,
    made_to_order: false,
    label: null,
  }
}

/** Reply to "Treba mi 25 m ograde za dvorište, visina oko 1,5 m". */
export const fenceClarify: ChatEvent[] = [
  { type: 'step', id: 'parse', text: 'Razumio: 25 m · dvorište · 150 cm', status: 'working' },
  { type: 'step', id: 'parse', text: 'Razumio: 25 m · dvorište · 150 cm', status: 'done' },
  { type: 'step', id: 'search', text: 'Pregledao 39 ograda', status: 'done' },
  { type: 'step', id: 'clarify', text: 'Trebam još 3 podatka', status: 'needs_input' },
  { type: 'token', text: 'Odlično — za 25 m dvorišta ' },
  { type: 'token', text: 'imamo dobra rješenja.' },
  {
    type: 'chips',
    intro: 'Još tri stvari:',
    // The "Vaš projekt" panel (web/2-clarify.html) before any answer.
    project: {
      title: 'Ograda za dvorište',
      length_m: 25,
      facts: [{ label: 'Visina', value: 'oko 150 cm' }],
    },
    questions: [
      // Corners, the "Ide okolo" toggle and the side fields; the answer goes
      // back as answers.sides (numbers) and answers.closed.
      { id: 'layout', text: 'Koliko ima kutova?', label: 'Kutovi', kind: 'layout', options: [], default: null },
      {
        id: 'posts',
        text: 'Gdje idu stupovi?',
        label: 'Stupovi',
        kind: 'single',
        options: [
          { value: 'plate', label: 'Na beton, s pločicom', short_label: null, badge: null, note: null },
          {
            value: 'concrete',
            label: 'U zemlju (betoniram)',
            short_label: 'U zemlju · po upitu',
            badge: 'po upitu · po narudžbi',
            note: 'Stupovi za betoniranje su po upitu i rade se po narudžbi, 10–15 dana. Beton ne prodajemo, nabavljate ga sami.',
          },
          { value: 'unknown', label: 'Ne znam', short_label: null, badge: null, note: null },
        ],
        // Base-plate posts are the default (fence-rules.md).
        default: 'plate',
      },
      {
        id: 'color',
        text: 'Koja boja?',
        label: 'Boja',
        kind: 'single',
        options: [
          { value: 'antracit', label: 'Antracit (RAL 7016)', short_label: 'Antracit', badge: null, note: null },
          { value: 'zelena', label: 'Zelena (RAL 6005)', short_label: 'Zelena', badge: null, note: null },
          { value: 'any', label: 'Svejedno', short_label: null, badge: null, note: null },
        ],
        default: null,
      },
    ],
  },
  { type: 'done', thread_id: THREAD_ID },
]

/** Reply to the answers: 2 corners, 10 + 5 + 10 m, na beton s pločicom, antracit. */
export const fenceBom: ChatEvent[] = [
  { type: 'step', id: 'bom', text: 'Izračunao popis materijala', status: 'working' },
  { type: 'step', id: 'bom', text: 'Izračunao popis materijala', status: 'done' },
  { type: 'token', text: 'Preporuka: 3D panelna ograda, 153 cm. ' },
  { type: 'token', text: 'Čvrsta i uredna, ne treba je zatezati.' },
  {
    type: 'product_cards',
    cards: [
      {
        listing_id: 63996799,
        url: 'https://martom.olx.ba/artikal/panelna-ograda-83-103-123-i-153cm-fi-420440mm-200-i-250cm-duzi-63996799',
        title: 'Panelna ograda 3D (Nylofor)',
        subtitle: 'Žica Ø 4,20/4,40 mm · plastificirana · antracit ili zelena',
        why: 'Čvrsta i uredna, ne treba je zatezati, a paneli od 250 cm daju najmanje stupova.',
        badge: 'Preporuka',
        photo_url: null,
        price: '53.00',
        price_unit: 'panel',
        variants: [
          { label: '103 × 250', price: '38.00' },
          { label: '153 × 250', price: '53.00' },
          { label: '153 × 200', price: null },
        ],
      },
      {
        listing_id: 24449780,
        url: 'https://martom.olx.ba/artikal/farmer-pletivo-ograda-za-zemljista-njive-sume-farme-24449780',
        title: 'Farmer pletivo',
        subtitle: 'Pocinčano pletivo za zemljišta, njive i farme',
        why: 'Jeftinija alternativa, ali traži zatezanje i više rada.',
        badge: 'Jeftinije',
        photo_url: null,
        price: '7.50',
        price_unit: 'm',
        variants: [],
      },
    ],
  },
  {
    type: 'bom',
    title: 'Popis materijala',
    summary: '25 m · panel 153 cm · antracit',
    lines: [
      line('panel', 'Panel 153 × 250 cm', '153 × 250 cm', 10, '53.00', '530.00'),
      line('post_base_plate', 'Stup s pločicom 155 cm', '155 cm', 11, '24.90', '273.90'),
      line('pvc_clip', 'PVC kopča', null, 52, '1.00', '52.00'),
      line('anchor_m8x80', 'Tiplo anker FI 10 / M8×80', 'M8×80', 44, '1.30', '57.20'),
    ],
    total: '913.10',
    unpriced_count: 0,
    extra_lines: [],
    notes: [NOTE],
  },
  { type: 'done', thread_id: THREAD_ID },
]

/**
 * The same answers, but the posts go in concrete ("U zemlju (betoniram)"):
 * no BOM calculation for those posts (fence-rules.md), the BOM keeps its
 * base-plate posts, and one no-price extra line goes to the cart and order.
 */
export const fenceConcretePosts: ChatEvent[] = [
  { type: 'step', id: 'bom', text: 'Izračunao popis materijala', status: 'done' },
  { type: 'token', text: 'Stupove za betoniranje radimo po narudžbi; cijenu javlja Martom.' },
  {
    type: 'bom',
    title: 'Popis materijala',
    summary: '25 m · panel 153 cm · antracit',
    lines: [
      line('panel', 'Panel 153 × 250 cm', '153 × 250 cm', 10, '53.00', '530.00'),
      line('post_base_plate', 'Stup s pločicom 155 cm', '155 cm', 11, '24.90', '273.90'),
      line('pvc_clip', 'PVC kopča', null, 52, '1.00', '52.00'),
      line('anchor_m8x80', 'Tiplo anker FI 10 / M8×80', 'M8×80', 44, '1.30', '57.20'),
    ],
    total: '913.10',
    unpriced_count: 0,
    extra_lines: [{ name: 'Stupovi za betoniranje – po upitu', note: 'Rade se po narudžbi, 10–15 dana.' }],
    notes: [NOTE],
  },
  { type: 'done', thread_id: THREAD_ID },
]

/**
 * A 25 m welded roll fence on foot-plate posts: the 100 cm support T-post has
 * no synced price yet, so it is "na upit" and the total covers the rest.
 */
export const rollFenceSomeOnRequest: ChatEvent[] = [
  { type: 'step', id: 'bom', text: 'Izračunao popis materijala', status: 'done' },
  { type: 'token', text: 'Evo popisa za mrežu u roli od 100 cm.' },
  {
    type: 'bom',
    title: 'Popis materijala',
    summary: '25 m · mreža u roli 100 cm · stupovi sa stopom',
    lines: [
      line('roll_welded', 'Varena ograda u roli 100 cm (25 m)', '100 cm', 1, '58.00', '58.00'),
      line('post_foot_plate', 'Stup sa stopom 100 cm', '100 cm', 14, '14.00', '196.00'),
      line('brace_t_post', 'Potporni stup 100 cm', '100 cm', 2, null, null),
      line('tensioner', 'Zatezač (španer)', null, 3, '1.65', '4.95'),
      line('anchor_m8x70', 'Anker M8×70', 'M8×70', 42, '0.80', '33.60'),
    ],
    total: '292.55',
    unpriced_count: 1,
    extra_lines: [],
    notes: [NOTE],
  },
  { type: 'done', thread_id: '6c2d9e14-5a3f-4b71-8e0d-2f9a4c6b1e57' },
]

/** Nothing on the list has a synced price yet: no total, never a fake "0.00". */
export const bomAllOnRequest: ChatEvent[] = [
  { type: 'step', id: 'bom', text: 'Izračunao popis materijala', status: 'done' },
  { type: 'token', text: 'Cijene za ove stavke potvrđuje Martom.' },
  {
    type: 'bom',
    title: 'Popis materijala',
    summary: '10 m · mreža u roli 100 cm',
    lines: [
      line('roll_welded', 'Varena ograda u roli 100 cm (25 m)', '100 cm', 1, null, null),
      line('brace_t_post', 'Potporni stup 100 cm', '100 cm', 2, null, null),
    ],
    total: null,
    unpriced_count: 2,
    extra_lines: [],
    notes: [NOTE],
  },
  { type: 'done', thread_id: '9e4b1a73-2c8d-4f05-b6e2-7d3a5c0f8b19' },
]
