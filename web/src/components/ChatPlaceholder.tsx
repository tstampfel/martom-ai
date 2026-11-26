import { copy } from '../copy'
import './ChatPlaceholder.css'

type ChatPlaceholderProps = {
  firstMessage: string
  onBack: () => void
}

/**
 * Stand-in for the chat screen: shows the customer's first message and makes
 * no API call. M5-3 replaces it with the streaming chat.
 */
export function ChatPlaceholder({ firstMessage, onBack }: ChatPlaceholderProps) {
  return (
    <section className="chat" aria-label={copy.chat.label}>
      <div className="chat__message">
        <span className="chat__author">{copy.chat.you}</span>
        <p className="chat__text">{firstMessage}</p>
      </div>
      <p className="chat__pending">{copy.chat.pending}</p>
      <button type="button" className="chat__back" onClick={onBack}>
        {copy.chat.back}
      </button>
    </section>
  )
}
