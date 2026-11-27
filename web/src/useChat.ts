import { useCallback, useEffect, useReducer, useRef } from 'react'
import type { ChatClient, ChatRequest, RequestAnswers } from './api/chatClient'
import { chatReducer, INITIAL_CHAT_STATE } from './chat'

/**
 * Runs one conversation: the first message streams on mount, and `send`
 * adds a customer message and streams its reply. Every event goes through
 * chatReducer. Starting a new reply, or leaving the chat, aborts the stream
 * still running.
 */
export function useChat(client: ChatClient, firstMessage: string) {
  // The first turn is in the initial state, so the effect below only streams;
  // under StrictMode's mount → unmount → mount it aborts the first stream and
  // starts again without adding a second turn.
  const [state, dispatch] = useReducer(chatReducer, firstMessage, (text) =>
    chatReducer(INITIAL_CHAT_STATE, { type: 'send', text }),
  )
  const controllerRef = useRef<AbortController | null>(null)
  // Read through a ref so a new client object (a parent re-render, a hot
  // reload) is used for the next message but never replays the first one.
  const clientRef = useRef(client)
  useEffect(() => {
    clientRef.current = client
  }, [client])

  const run = useCallback(
    async (request: ChatRequest) => {
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller
      try {
        for await (const event of clientRef.current.stream(request, controller.signal)) {
          if (controller.signal.aborted) return
          dispatch({ type: 'event', event })
        }
        if (!controller.signal.aborted) dispatch({ type: 'stream_ended' })
      } catch {
        if (!controller.signal.aborted) dispatch({ type: 'stream_failed' })
      }
    },
    [],
  )

  useEffect(() => {
    void run({ message: firstMessage, thread_id: null, answers: null })
    return () => controllerRef.current?.abort()
  }, [run, firstMessage])

  function send(text: string, answers: RequestAnswers | null = null) {
    dispatch({ type: 'send', text })
    void run({ message: text, thread_id: state.threadId, answers })
  }

  return { state, send }
}
