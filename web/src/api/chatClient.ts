// The one interface the chat screen talks to. The fake client
// (fakeChatClient.ts) replays recorded fixture streams; the real SSE client
// for POST /api/chat (#25) implements the same interface, so the screen
// doesn't change when it lands.

// The request body is mirrored by hand from docs/api/chat-request.schema.json,
// which the backend generates from src/martom/api/chat_request.py;
// chatClient.test.ts checks the two agree. Contract: docs/api/chat-events.md.

/** The longest fence side in metres; a longer one gets no BOM (bom/common.py). */
export const MAX_SIDE_M = 300

/** The customer's answers to a chips event. */
export type RequestAnswers = {
  /** Question id → picked option value, for the single questions. */
  choices: Record<string, string>
  /**
   * Length of each fence side in metres, in order, each 0 < x ≤ MAX_SIDE_M.
   * Empty without a layout question or for "Ne znam" corners. Never a string:
   * "10 + 5 + 10" is display text only.
   */
  sides: number[]
  /**
   * The fence goes all the way round; needs at least 3 sides. Corners are
   * never sent: len(sides) - 1 when open, len(sides) when closed.
   */
  closed: boolean
}

export type ChatRequest = {
  /** The customer's message as typed, or the summary of their chip answers. */
  message: string
  /** From the previous reply's done event; null for the first message. */
  thread_id: string | null
  /** Set when the message answers a chips event. */
  answers: RequestAnswers | null
}

export interface ChatClient {
  /**
   * Streams the reply's events in order. Each value is one parsed SSE message;
   * it is `unknown` because the chat checks it (chat.ts) instead of trusting
   * the network. Stops early when `signal` aborts; throws when the connection
   * fails.
   */
  stream(request: ChatRequest, signal?: AbortSignal): AsyncIterable<unknown>
}
