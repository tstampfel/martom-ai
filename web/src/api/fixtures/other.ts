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
        listing_id: 36376333,
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
