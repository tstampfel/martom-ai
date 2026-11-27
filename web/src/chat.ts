// The conversation state, built from the events of POST /api/chat
// (web/src/api/events.ts). This file is pure: every incoming event goes
// through chatReducer, so the whole chat can be tested by replaying the
// recorded fixture streams, without timers or React.

import {
  CHAT_EVENT_TYPES,
  type BomEvent,
  type ChatEvent,
  type ChipsEvent,
  type ErrorEvent,
  type ProductCard,
  type StepEvent,
} from './api/events'

/**
 * Why a reply failed. The first three come from the backend's error event;
 * the last two are the browser's own: an event the contract doesn't know,
 * and a stream that broke or ended without its done event.
 */
export type ChatErrorKind = ErrorEvent['kind'] | 'unknown_event' | 'connection'

export type ChatError = {
  kind: ChatErrorKind
  /** Only for rate_limited. */
  retryAfterSeconds: number | null
}

/** The advisor's answer to one customer message. */
export type AdvisorReply = {
  /** In order of first arrival; a step sent again with the same id is updated in place. */
  steps: StepEvent[]
  text: string
  chips: ChipsEvent | null
  cards: ProductCard[]
  bom: BomEvent | null
  error: ChatError | null
  /** True until the done event (or a failure) arrives. */
  streaming: boolean
}

export type Turn = {
  /** The customer's message, as shown in their bubble. */
  text: string
  reply: AdvisorReply
}

export type ChatState = {
  turns: Turn[]
  /** From the last done event; sent with the next message. */
  threadId: string | null
}

export type ChatAction =
  | { type: 'send'; text: string }
  /** A parsed SSE message. `unknown` on purpose: it is checked here, not trusted. */
  | { type: 'event'; event: unknown }
  | { type: 'stream_failed' }
  | { type: 'stream_ended' }

export const INITIAL_CHAT_STATE: ChatState = { turns: [], threadId: null }

const EMPTY_REPLY: AdvisorReply = {
  steps: [],
  text: '',
  chips: null,
  cards: [],
  bom: null,
  error: null,
  streaming: true,
}

const KNOWN_TYPES: ReadonlySet<string> = new Set(CHAT_EVENT_TYPES)

/**
 * Returns the value as a ChatEvent when its `type` is one the contract knows,
 * else null. Only `type` is checked: the fields of known events are held to
 * the contract by the backend's models and the fixture tests.
 */
export function asChatEvent(value: unknown): ChatEvent | null {
  if (typeof value !== 'object' || value === null || !('type' in value)) return null
  return typeof value.type === 'string' && KNOWN_TYPES.has(value.type) ? (value as ChatEvent) : null
}

function upsertStep(steps: StepEvent[], step: StepEvent): StepEvent[] {
  const index = steps.findIndex((existing) => existing.id === step.id)
  if (index === -1) return [...steps, step]
  return steps.map((existing, i) => (i === index ? step : existing))
}

/** Applies one event to the reply. After an error only done still counts. */
function applyEvent(reply: AdvisorReply, event: ChatEvent): AdvisorReply {
  if (reply.error && event.type !== 'done') return reply
  switch (event.type) {
    case 'token':
      return { ...reply, text: reply.text + event.text }
    case 'step':
      return { ...reply, steps: upsertStep(reply.steps, event) }
    case 'chips':
      return { ...reply, chips: event }
    case 'product_cards':
      return { ...reply, cards: [...reply.cards, ...event.cards] }
    case 'bom':
      return { ...reply, bom: event }
    case 'error':
      return { ...reply, error: { kind: event.kind, retryAfterSeconds: event.retry_after_seconds } }
    case 'done':
      return { ...reply, streaming: false }
  }
}

function withLastReply(state: ChatState, update: (reply: AdvisorReply) => AdvisorReply): ChatState {
  const last = state.turns.at(-1)
  if (!last) return state
  const reply = update(last.reply)
  if (reply === last.reply) return state
  return { ...state, turns: [...state.turns.slice(0, -1), { ...last, reply }] }
}

function failed(kind: ChatErrorKind) {
  return (reply: AdvisorReply): AdvisorReply =>
    reply.streaming ? { ...reply, streaming: false, error: reply.error ?? { kind, retryAfterSeconds: null } } : reply
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'send':
      return { ...state, turns: [...state.turns, { text: action.text, reply: EMPTY_REPLY }] }
    case 'event': {
      const event = asChatEvent(action.event)
      if (event === null) {
        return withLastReply(state, (reply) =>
          reply.error ? reply : { ...reply, error: { kind: 'unknown_event', retryAfterSeconds: null } },
        )
      }
      const next = withLastReply(state, (reply) => applyEvent(reply, event))
      return event.type === 'done' ? { ...next, threadId: event.thread_id } : next
    }
    case 'stream_failed':
    case 'stream_ended':
      return withLastReply(state, failed('connection'))
  }
}
