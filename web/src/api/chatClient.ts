// The one interface the chat screen talks to. The fake client
// (fakeChatClient.ts) replays recorded fixture streams; the real SSE client
// for POST /api/chat (#25) implements the same interface, so the screen
// doesn't change when it lands.

export type ChatRequest = {
  /** The customer's message as typed, or the summary of their chip answers. */
  message: string
  /** From the previous reply's done event; null for the first message. */
  threadId: string | null
  /**
   * The clarify answers, question id → option value (side lengths as
   * "10 + 5 + 10"), when the message answers a chips event. Provisional:
   * #25 settles how answers reach the backend.
   */
  answers: Record<string, string> | null
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
