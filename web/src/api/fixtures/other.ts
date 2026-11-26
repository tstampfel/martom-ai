// Replies that are not a fence materials list: a plain product question, a
// failed reply and a rate-limited one. Listing ids, slugs and prices are
// illustrative, not live catalogue data.

import type { ChatEvent } from '../events'

/** Reply to "Imate li španer za žicu?" (a non-fence product question). */
export const productQuestion: ChatEvent[] = [
  { type: 'step', id: 'parse', text: 'Razumio: španer za žicu', status: 'done' },
  { type: 'step', id: 'search', text: 'Pregledao 6 proizvoda', status: 'done' },
  { type: 'token', text: 'Imamo španer za zateznu žicu, ' },
  { type: 'token', text: 'pocinčani, za žicu do 3,1 mm.' },
  {
    type: 'product_cards',
    cards: [
      {
        listing_id: 18129143,
        url: 'https://martom.olx.ba/artikal/spaner-za-zicu-18129143',
        title: 'Španer za žicu',
        subtitle: 'Pocinčani · za žicu do 3,1 mm',
        why: 'Zateže žicu na ogradi u roli; jedan po žici na svakom zateznom stupu.',
        badge: null,
        photo_url: null,
        price: '2.50',
        price_unit: 'kom',
        variants: [],
      },
    ],
  },
  { type: 'done', thread_id: '8d1e4c52-0b9f-4a6e-bf3c-5a7d2e9c1f04' },
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
