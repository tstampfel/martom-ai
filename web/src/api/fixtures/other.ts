// Replies that are not a fence materials list: a plain product question, a
// failed reply and a rate-limited one. Listing 36376333 and its slug are
// recorded (tests/fixtures/olx); its prices are illustrative.

import type { ChatEvent } from '../events'

/** Reply to "Imate li dugi katanac?" (a non-fence product question). */
export const productQuestion: ChatEvent[] = [
  { type: 'step', id: 'parse', text: 'Razumio: dugi katanac', status: 'done' },
  { type: 'step', id: 'search', text: 'Pregledao 6 proizvoda', status: 'done' },
  { type: 'token', text: 'Imamo dugi katanac, ' },
  { type: 'token', text: 'u dvije veličine.' },
  {
    type: 'product_cards',
    cards: [
      {
        source: 'olx',
        listing_id: 36376333,
        manual_key: null,
        url: 'https://martom.olx.ba/artikal/katanac-dugi-5040-i-30mm-ibfm-36376333',
        title: 'Katanac dugi 50/40 i 30 mm',
        subtitle: 'IBFM',
        why: 'Dugi luk prolazi kroz kapiju i lanac.',
        badge: null,
        photo_url: null,
        price: '12.00',
        price_unit: 'kom',
        variants: [
          { label: '30 mm', price: '8.00' },
          { label: '50/40 mm', price: '12.00' },
        ],
      },
    ],
  },
  { type: 'done', thread_id: '8d1e4c52-0b9f-4a6e-bf3c-5a7d2e9c1f04' },
]

/**
 * Reply to "Imate li anker M8x70?": a product Martom sells without an OLX
 * listing (manual_products.toml, #132), so no listing id and no link.
 */
export const manualProduct: ChatEvent[] = [
  { type: 'step', id: 'parse', text: 'Razumio: anker M8×70', status: 'done' },
  { type: 'token', text: 'Imamo anker M8×70.' },
  {
    type: 'product_cards',
    cards: [
      {
        source: 'manual',
        listing_id: null,
        manual_key: 'anchor_m8x70',
        url: null,
        title: 'Anker M8×70',
        subtitle: null,
        why: 'Za stup s pločicom na betonu.',
        badge: null,
        photo_url: null,
        price: '0.80',
        price_unit: 'kom',
        variants: [],
      },
    ],
  },
  { type: 'done', thread_id: '3c7b9e2a-6f1d-4a85-9b0e-2d4f8a1c6e53' },
]

/** The agent failed midway: some text arrived, then an internal error. */
export const internalError: ChatEvent[] = [
  { type: 'step', id: 'parse', text: 'Razumio: panelna ograda', status: 'done' },
  { type: 'step', id: 'search', text: 'Tražim ograde', status: 'working' },
  { type: 'token', text: 'Gledam koje ' },
  { type: 'error', kind: 'internal', retry_after_seconds: null },
  { type: 'done', thread_id: 'f2a9c7e1-3d4b-4c8a-9e6f-1b5d0a7c3e82' },
]

/** Too many messages: the reply is refused before the agent starts. */
export const rateLimited: ChatEvent[] = [
  { type: 'error', kind: 'rate_limited', retry_after_seconds: 30 },
  { type: 'done', thread_id: 'f2a9c7e1-3d4b-4c8a-9e6f-1b5d0a7c3e82' },
]
