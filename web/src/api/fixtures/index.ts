// Recorded POST /api/chat event streams, one array per reply. The frontend's
// fake stream replays these until the real endpoint (#21) exists, and
// events.test.ts checks every event against events.ts and the JSON Schema.

import type { ChatEvent } from '../events'
import { bomAllOnRequest, fenceBom, fenceClarify, rollFenceSomeOnRequest } from './fence'
import { internalError, productQuestion, rateLimited } from './other'

export const fixtures = {
  fenceClarify,
  fenceBom,
  rollFenceSomeOnRequest,
  bomAllOnRequest,
  productQuestion,
  internalError,
  rateLimited,
} satisfies Record<string, ChatEvent[]>
