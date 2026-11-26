import { useState } from 'react'
import { AppShell } from './components/AppShell'
import { ChatPlaceholder } from './components/ChatPlaceholder'
import { StartScreen } from './components/StartScreen'

/**
 * Switches between the start screen and the chat. `startChat` is the one
 * entry point that the question box, the quick-start chips and the header
 * categories all call; the real chat (M5-3) builds on it.
 */
export default function App() {
  // null = start screen; otherwise the customer's first message.
  const [firstMessage, setFirstMessage] = useState<string | null>(null)

  function startChat(text: string) {
    setFirstMessage(text)
  }

  return (
    <AppShell onStartChat={startChat}>
      {firstMessage === null ? (
        <StartScreen onStartChat={startChat} />
      ) : (
        <ChatPlaceholder firstMessage={firstMessage} onBack={() => setFirstMessage(null)} />
      )}
    </AppShell>
  )
}
