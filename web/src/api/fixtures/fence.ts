// A fence conversation as two replies on one thread: the customer asks for
// 25 m of yard fence, the advisor asks three clarify questions (2-clarify.html),
// the customer answers and gets products and a materials list (3-results.html).
// Listing ids, slugs and prices are illustrative, not live catalogue data.

import type { ChatEvent } from '../events'

const THREAD_ID = '3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10'

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
    questions: [
      {
        id: 'corners',
        text: 'Koliko ima kutova?',
        kind: 'single',
        options: [
          { value: '0', label: '0', badge: null, note: null },
          { value: '1', label: '1', badge: null, note: null },
          { value: '2', label: '2', badge: null, note: null },
          { value: '3', label: '3', badge: null, note: null },
          { value: '4+', label: '4+', badge: null, note: null },
          { value: 'unknown', label: 'Ne znam', badge: null, note: null },
        ],
      },
      { id: 'sides', text: 'Dužina svake strane (m)', kind: 'side_lengths', options: [] },
      {
        id: 'posts',
        text: 'Gdje idu stupovi?',
        kind: 'single',
        options: [
          { value: 'plate', label: 'Na beton, s pločicom', badge: null, note: null },
          {
            value: 'concrete',
            label: 'U zemlju (betoniram)',
            badge: 'po narudžbi, 10–15 dana',
            note: 'Stupovi za betoniranje su po narudžbi, 10–15 dana. Beton ne prodajemo, nabavljate ga sami.',
          },
          { value: 'unknown', label: 'Ne znam', badge: null, note: null },
        ],
      },
      {
        id: 'color',
        text: 'Koja boja?',
        kind: 'single',
        options: [
          { value: 'antracit', label: 'Antracit (RAL 7016)', badge: null, note: null },
          { value: 'zelena', label: 'Zelena (RAL 6005)', badge: null, note: null },
          { value: 'any', label: 'Svejedno', badge: null, note: null },
        ],
      },
    ],
  },
  { type: 'done', thread_id: THREAD_ID },
]

/** Reply to the answers: 2 corners, 10 + 5 + 10 m, base-plate posts, antracit. */
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
        url: 'https://martom.olx.ba/artikal/panelna-ograda-3d-nylofor-63996799',
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
        listing_id: 51234567,
        url: 'https://martom.olx.ba/artikal/mreza-u-roli-garden-light-51234567',
        title: 'Mreža u roli Garden Light',
        subtitle: 'Plastificirana · okce 76 × 50 mm · rola 25 m · Betafence',
        why: 'Jeftinija alternativa, ali traži zateznu žicu i više rada.',
        badge: 'Jeftinije',
        photo_url: null,
        price: '7.50',
        price_unit: 'm',
        variants: [
          { label: '122 cm', price: '6.00' },
          { label: '150 cm', price: '7.50' },
          { label: '183 cm', price: '9.00' },
        ],
      },
    ],
  },
  {
    type: 'bom',
    title: 'Popis materijala',
    summary: '25 m · panel 153 cm · antracit',
    lines: [
      {
        item: 'panel',
        name: 'Panel 153 × 250 cm, antracit',
        size: '153 × 250 cm',
        quantity: 10,
        unit_price: '53.00',
        line_total: '530.00',
        price_on_request: false,
        made_to_order: false,
        label: null,
      },
      {
        item: 'post_base_plate',
        name: 'Stup s pločicom 155 cm, antracit',
        size: '155 cm',
        quantity: 11,
        unit_price: null,
        line_total: null,
        price_on_request: true,
        made_to_order: false,
        label: null,
      },
      {
        item: 'pvc_clip',
        name: 'Obujmica PVC 50 × 50 mm',
        size: null,
        quantity: 52,
        unit_price: '1.50',
        line_total: '78.00',
        price_on_request: false,
        made_to_order: false,
        label: null,
      },
      {
        item: 'anchor_m8x80',
        name: 'Anker M8 × 80',
        size: 'M8 × 80',
        quantity: 44,
        unit_price: '0.80',
        line_total: '35.20',
        price_on_request: false,
        made_to_order: false,
        label: null,
      },
    ],
    total: '643.20',
    notes: ['Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. Dostava se potvrđuje.'],
  },
  { type: 'done', thread_id: THREAD_ID },
]
