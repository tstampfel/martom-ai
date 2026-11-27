import { describe, expect, it } from 'vitest'
import { fixtures } from './api/fixtures'
import { chatReducer, INITIAL_CHAT_STATE, type ChatState } from './chat'

/** Sends `text`, then replays `events` as one reply. */
function replay(events: readonly unknown[], text = 'Treba mi 25 m ograde', state: ChatState = INITIAL_CHAT_STATE) {
  let next = chatReducer(state, { type: 'send', text })
  for (const event of events) next = chatReducer(next, { type: 'event', event })
  return next
}

function lastReply(state: ChatState) {
  const turn = state.turns.at(-1)
  if (!turn) throw new Error('no turn')
  return turn.reply
}

describe('chatReducer', () => {
  it('adds a turn with the customer text and an empty, streaming reply', () => {
    const state = chatReducer(INITIAL_CHAT_STATE, { type: 'send', text: 'Dvorište' })
    expect(state.turns).toHaveLength(1)
    expect(state.turns[0].text).toBe('Dvorište')
    expect(lastReply(state)).toMatchObject({ text: '', steps: [], streaming: true, error: null })
  })

  it('appends tokens to the reply text', () => {
    const state = replay([
      { type: 'token', text: 'Odlično — ' },
      { type: 'token', text: 'imamo.' },
    ])
    expect(lastReply(state).text).toBe('Odlično — imamo.')
  })

  it('updates a step with the same id in place and keeps the order of first arrival', () => {
    const state = replay(fixtures.fenceClarify)
    expect(lastReply(state).steps.map((step) => [step.id, step.status])).toEqual([
      ['parse', 'done'],
      ['search', 'done'],
      ['clarify', 'needs_input'],
    ])
  })

  it('keeps the chips, the product cards and the materials list of the reply', () => {
    const clarify = replay(fixtures.fenceClarify)
    expect(lastReply(clarify).chips?.questions.map((question) => question.id)).toEqual([
      'corners',
      'closed',
      'sides',
      'posts',
      'color',
    ])
    const results = replay(fixtures.fenceBom)
    expect(lastReply(results).cards.map((card) => card.listing_id)).toEqual([63996799, 24449780])
    expect(lastReply(results).bom?.total).toBe('913.10')
  })

  it('stops streaming and remembers the thread id on done', () => {
    const state = replay(fixtures.fenceClarify)
    expect(lastReply(state).streaming).toBe(false)
    expect(state.threadId).toBe('3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10')
  })

  it('keeps the thread id across turns and starts the next reply empty', () => {
    const first = replay(fixtures.fenceClarify)
    const second = chatReducer(first, { type: 'send', text: 'Antracit' })
    expect(second.threadId).toBe(first.threadId)
    expect(second.turns).toHaveLength(2)
    expect(lastReply(second)).toMatchObject({ text: '', chips: null, cards: [], streaming: true })
  })

  it('shows an error event with its kind and retry time, and still takes the thread id from done', () => {
    const state = replay(fixtures.rateLimited)
    expect(lastReply(state).error).toEqual({ kind: 'rate_limited', retryAfterSeconds: 30 })
    expect(lastReply(state).streaming).toBe(false)
    expect(state.threadId).toBe('f2a9c7e1-3d4b-4c8a-9e6f-1b5d0a7c3e82')
  })

  it('keeps the text that arrived before an internal error', () => {
    const state = replay(fixtures.internalError)
    expect(lastReply(state).text).toBe('Gledam koje ')
    expect(lastReply(state).error?.kind).toBe('internal')
  })

  it('shows an unknown event type as an error instead of dropping it', () => {
    const state = replay([
      { type: 'token', text: 'Evo ' },
      { type: 'surprise', text: 'x' },
    ])
    expect(lastReply(state).error?.kind).toBe('unknown_event')
  })

  it('treats a value that is not an event object as an unknown event', () => {
    for (const bad of [null, 'token', 42, { text: 'no type' }]) {
      expect(lastReply(replay([bad])).error?.kind).toBe('unknown_event')
    }
  })

  it('ignores content after an error but still takes the thread id', () => {
    const state = replay([
      { type: 'surprise' },
      { type: 'token', text: 'kasno' },
      { type: 'done', thread_id: 't-1' },
    ])
    expect(lastReply(state).text).toBe('')
    expect(lastReply(state).error?.kind).toBe('unknown_event')
    expect(state.threadId).toBe('t-1')
  })

  it('shows a connection error when the stream fails', () => {
    let state = chatReducer(INITIAL_CHAT_STATE, { type: 'send', text: 'x' })
    state = chatReducer(state, { type: 'stream_failed' })
    expect(lastReply(state)).toMatchObject({ streaming: false, error: { kind: 'connection' } })
  })

  it('shows a connection error when the stream ends without done', () => {
    const state = chatReducer(replay([{ type: 'token', text: 'pola' }]), { type: 'stream_ended' })
    expect(lastReply(state)).toMatchObject({ streaming: false, error: { kind: 'connection' } })
  })

  it('changes nothing when the stream ends after done', () => {
    const done = replay(fixtures.productQuestion)
    expect(chatReducer(done, { type: 'stream_ended' })).toBe(done)
  })
})
