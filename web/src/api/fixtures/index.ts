// Recorded POST /api/chat event streams, one array per reply. The frontend's
// fake stream replays these until the real endpoint (#21) exists, and
// events.test.ts checks every event against events.ts and the JSON Schema.

import type { ChatEvent } from '../events'
import { bomAllOnRequest, fenceBom, fenceChanged, fenceClarify, fenceConcretePosts, rollFenceSomeOnRequest } from './fence'
import { cartAdded, chainLink, internalError, manualProduct, productQuestion, rateLimited } from './other'

export const fixtures = {
  fenceClarify,
  fenceBom,
  fenceConcretePosts,
  rollFenceSomeOnRequest,
  bomAllOnRequest,
  fenceChanged,
  productQuestion,
  manualProduct,
  cartAdded,
  chainLink,
  internalError,
  rateLimited,
} satisfies Record<string, ChatEvent[]>
