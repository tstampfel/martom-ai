// The events POST /api/chat streams (Server-Sent Events), one JSON object per
// SSE message, told apart by `type`.
//
// Mirrored by hand from docs/api/chat-events.schema.json, which the backend
// generates from src/martom/api/chat_events.py. events.test.ts checks the
// fixtures against this file (via typecheck) and the schema (via Ajv), so the
// two can't drift apart unnoticed. Contract and examples: docs/api/chat-events.md.
//
// Every field is always present; an empty value is null. Prices are strings
// with two decimals and a dot ("53.00", KM); null means "cijena na upit".

/** A price in KM as sent by the API, e.g. "53.00". Format it for display. */
export type Price = string

export const CHAT_EVENT_TYPES = ['token', 'step', 'chips', 'product_cards', 'bom', 'error', 'done'] as const

/** What a BOM line is (src/martom/bom/common.py, Item). */
export const BOM_ITEMS = [
  'panel',
  'post_base_plate',
  'post_concrete',
  'pvc_clip',
  'anchor_m8x80',
  'roll_welded',
  'post_t',
  'post_foot_plate',
  'brace_t_post',
  'tensioner',
  'tension_wire',
  'wire_holder',
  'anchor_m8x70',
] as const

export type BomItem = (typeof BOM_ITEMS)[number]

/** A piece of the answer text; append it to the current message. */
export interface TokenEvent {
  type: 'token'
  text: string
}

/** A "Savjetnik radi" step; sent again with the same id when its status changes. */
export interface StepEvent {
  type: 'step'
  id: string
  text: string
  status: 'working' | 'done' | 'needs_input'
}

export interface ChipOption {
  /** Sent back in the next message; not shown. */
  value: string
  label: string
  /** e.g. "po narudžbi, 10–15 dana". */
  badge: string | null
  /** Shown under the question when this option is picked. */
  note: string | null
}

export interface ChipQuestion {
  id: string
  text: string
  /** single: pick one option. side_lengths: one input (m) per side, no options. */
  kind: 'single' | 'side_lengths'
  options: ChipOption[]
}

/** The clarify questions the advisor still needs answered. */
export interface ChipsEvent {
  type: 'chips'
  intro: string | null
  questions: ChipQuestion[]
}

export interface Variant {
  label: string
  price: Price | null
}

export interface ProductCard {
  listing_id: number
  /** https://martom.olx.ba/artikal/{slug}; the slug ends in listing_id. */
  url: string
  title: string
  subtitle: string | null
  /** Why it fits this customer. */
  why: string
  /** e.g. "Preporuka", "Jeftinije". */
  badge: string | null
  photo_url: string | null
  /** Price of the shown variant per price_unit; null: "na upit". */
  price: Price | null
  /** e.g. "panel", "m", "kom". */
  price_unit: string | null
  variants: Variant[]
}

export interface ProductCardsEvent {
  type: 'product_cards'
  cards: ProductCard[]
}

export interface BomLine {
  item: BomItem
  name: string
  size: string | null
  quantity: number
  /** null when price_on_request. */
  unit_price: Price | null
  /** unit_price × quantity; null when price_on_request. */
  line_total: Price | null
  /** true: show "cijena na upit", no price. */
  price_on_request: boolean
  /** "po narudžbi": Toni confirms it. */
  made_to_order: boolean
  /** e.g. "po narudžbi, 10–15 dana". */
  label: string | null
}

/** The materials list (popis materijala). */
export interface BomEvent {
  type: 'bom'
  title: string
  summary: string
  lines: BomLine[]
  /** Sum of the priced lines; lines "na upit" are left out. */
  total: Price
  notes: string[]
}

/** The reply failed. Show the copy for `kind`; a done event follows. */
export interface ErrorEvent {
  type: 'error'
  kind: 'rate_limited' | 'invalid_request' | 'internal'
  /** Only for rate_limited: seconds until the customer may try again. */
  retry_after_seconds: number | null
}

/** The last event of every reply, also after an error. */
export interface DoneEvent {
  type: 'done'
  /** Send it with the next message to continue the conversation. */
  thread_id: string
}

export type ChatEvent =
  | TokenEvent
  | StepEvent
  | ChipsEvent
  | ProductCardsEvent
  | BomEvent
  | ErrorEvent
  | DoneEvent

// Compile-time guard: CHAT_EVENT_TYPES and the union's `type` values must be
// the same set. If either side gains a type, typecheck fails here.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false
const typesMatch: Same<(typeof CHAT_EVENT_TYPES)[number], ChatEvent['type']> = true
void typesMatch
