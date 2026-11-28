import { useState } from 'react'
import type { BomRecalculator } from './api/bomRecalculator'
import type { ChatClient } from './api/chatClient'
import { createFakeBomRecalculator } from './api/fakeBomRecalculator'
import { createFakeChatClient } from './api/fakeChatClient'
import { AppShell } from './components/AppShell'
import { ChatView } from './components/ChatView'
import { PrivacyPage } from './components/PrivacyPage'
import { StartScreen } from './components/StartScreen'
import { isPrivacyPath, PRIVACY_PATH } from './routes'

// Replays the recorded fixture streams until the real SSE client lands (#25).
const defaultChatClient = createFakeChatClient()
// Recalculates a materials list from the event's own prices until the real
// API call lands (#26).
const defaultBomRecalculator = createFakeBomRecalculator()

type AppProps = {
  /** Where replies come from; tests pass a fake that doesn't wait. */
  chatClient?: ChatClient
  /** Where materials list quantity edits are recalculated; tests pass one that doesn't wait. */
  bomRecalculator?: BomRecalculator
  /** The URL path; tests pass one. /privatnost shows the privacy page. */
  path?: string
}

/**
 * Shows the privacy page on /privatnost and the advisor everywhere else.
 */
export default function App({ path = window.location.pathname, ...advisorProps }: AppProps) {
  return isPrivacyPath(path) ? <PrivacyPage /> : <Advisor {...advisorProps} />
}

/**
 * Switches between the start screen and the chat. `startChat` is the one
 * entry point that the question box, the quick-start chips and the header
 * categories all call.
 */
function Advisor({ chatClient = defaultChatClient, bomRecalculator = defaultBomRecalculator }: Omit<AppProps, 'path'>) {
  // null = start screen; otherwise the chat opened with this first message.
  const [chat, setChat] = useState<{ id: number; firstMessage: string } | null>(null)
  // After the first chat, coming back focuses the question box. On the very
  // first visit it doesn't, so phones don't pop up the keyboard on load.
  const [hasChatted, setHasChatted] = useState(false)

  function startChat(text: string) {
    // A new id remounts the chat, so a header category picked mid-chat
    // starts a fresh conversation.
    setChat((current) => ({ id: (current?.id ?? 0) + 1, firstMessage: text }))
    setHasChatted(true)
  }

  return (
    <AppShell onStartChat={startChat} showPrivacyNotice={chat === null} privacyHref={PRIVACY_PATH}>
      {chat === null ? (
        <StartScreen onStartChat={startChat} focusQuestion={hasChatted} />
      ) : (
        <ChatView
          key={chat.id}
          firstMessage={chat.firstMessage}
          client={chatClient}
          recalculator={bomRecalculator}
          onBack={() => setChat(null)}
        />
      )}
    </AppShell>
  )
}
