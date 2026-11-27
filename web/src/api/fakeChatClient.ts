// A ChatClient that replays the recorded fixture streams with realistic
// timing, so the chat UI can be built and tried before POST /api/chat exists
// (#25 replaces it with the real SSE client).

import type { ChatClient, ChatRequest } from './chatClient'
import type { ChatEvent } from './events'
import { fixtures } from './fixtures'

/** Milliseconds before each event: tokens stream fast, steps take a moment. */
export function eventDelay(event: ChatEvent): number {
  switch (event.type) {
    case 'token':
      return 60
    case 'step':
      return 500
    case 'done':
      return 0
    default:
      return 300
  }
}

const PRODUCT_WORDS = /katanac|brusilic|alat|vijak|boj[ae]/i

/**
 * Picks the fixture stream for a request: chip answers get the materials list
 * (the concrete-posts variant when the posts go in concrete), a product
 * question gets product cards, anything else the fence clarify questions.
 * "fake:<fixture name>" (e.g. "fake:rateLimited") replays that fixture, so
 * the error states can be tried in the browser.
 */
export function pickFakeReply(request: ChatRequest): readonly unknown[] {
  const named = /^fake:(\w+)$/.exec(request.message.trim())?.[1]
  if (named && Object.hasOwn(fixtures, named)) return fixtures[named as keyof typeof fixtures]
  if (request.answers) {
    return request.answers.choices.posts === 'concrete' ? fixtures.fenceConcretePosts : fixtures.fenceBom
  }
  if (PRODUCT_WORDS.test(request.message)) return fixtures.productQuestion
  return fixtures.fenceClarify
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

type FakeOptions = {
  /** Which events to replay; defaults to pickFakeReply. */
  pickReply?: (request: ChatRequest) => readonly unknown[]
  /** How to wait; tests pass one that resolves at once. */
  wait?: (ms: number) => Promise<void>
}

export function createFakeChatClient({ pickReply = pickFakeReply, wait = sleep }: FakeOptions = {}): ChatClient {
  return {
    async *stream(request, signal) {
      for (const event of pickReply(request)) {
        // Unknown values (tests feed some) wait like a plain event.
        await wait(typeof event === 'object' && event !== null && 'type' in event ? eventDelay(event as ChatEvent) : 300)
        if (signal?.aborted) return
        yield event
      }
    },
  }
}
