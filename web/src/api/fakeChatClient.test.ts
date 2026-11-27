import { describe, expect, it } from 'vitest'
import { createFakeChatClient, eventDelay, pickFakeReply } from './fakeChatClient'
import { fixtures } from './fixtures'

const noWait = () => Promise.resolve()

async function collect(iterable: AsyncIterable<unknown>): Promise<unknown[]> {
  const out: unknown[] = []
  for await (const event of iterable) out.push(event)
  return out
}

describe('createFakeChatClient', () => {
  it('replays the picked fixture events in order', async () => {
    const client = createFakeChatClient({ wait: noWait, pickReply: () => fixtures.productQuestion })
    const events = await collect(client.stream({ message: 'Imate li dugi katanac?', thread_id: null, answers: null }))
    expect(events).toEqual(fixtures.productQuestion)
  })

  it('waits before every event, using the realistic delay for its type', async () => {
    const waits: number[] = []
    const client = createFakeChatClient({
      wait: (ms) => {
        waits.push(ms)
        return Promise.resolve()
      },
      pickReply: () => fixtures.rateLimited,
    })
    await collect(client.stream({ message: 'x', thread_id: null, answers: null }))
    expect(waits).toEqual(fixtures.rateLimited.map(eventDelay))
  })

  it('stops when the request is aborted', async () => {
    const controller = new AbortController()
    const client = createFakeChatClient({ wait: noWait, pickReply: () => fixtures.fenceBom })
    const events: unknown[] = []
    for await (const event of client.stream({ message: 'x', thread_id: null, answers: null }, controller.signal)) {
      events.push(event)
      if (events.length === 2) controller.abort()
    }
    expect(events).toHaveLength(2)
  })
})

describe('eventDelay', () => {
  it('streams tokens faster than steps', () => {
    expect(eventDelay({ type: 'token', text: 'a' })).toBeLessThan(eventDelay({ type: 'step', id: 'a', text: 'a', status: 'done' }))
  })
})

describe('pickFakeReply', () => {
  it('asks the clarify questions for a fence question', () => {
    expect(pickFakeReply({ message: 'Treba mi 25 m ograde', thread_id: null, answers: null })).toBe(fixtures.fenceClarify)
  })

  it('answers the clarify answers with products and a materials list', () => {
    const answers = { choices: { posts: 'plate', color: 'antracit' }, sides: [10, 5, 10], closed: false }
    expect(pickFakeReply({ message: 'Antracit', thread_id: 't', answers })).toBe(fixtures.fenceBom)
  })

  it('answers concrete posts with the concrete-posts reply', () => {
    const answers = { choices: { posts: 'concrete' }, sides: [], closed: false }
    expect(pickFakeReply({ message: 'U zemlju', thread_id: 't', answers })).toBe(fixtures.fenceConcretePosts)
  })

  it('replays a fixture by name for "fake:<name>", to try the error states', () => {
    expect(pickFakeReply({ message: 'fake:rateLimited', thread_id: null, answers: null })).toBe(fixtures.rateLimited)
    expect(pickFakeReply({ message: 'fake:nope', thread_id: null, answers: null })).toBe(fixtures.fenceClarify)
  })

  it('answers a product question with product cards only', () => {
    expect(pickFakeReply({ message: 'Imate li dugi katanac?', thread_id: null, answers: null })).toBe(
      fixtures.productQuestion,
    )
  })
})
