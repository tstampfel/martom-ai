import { useEffect, useRef } from 'react'
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
  const sectionRef = useRef<HTMLElement>(null)

  // The control that opened the chat is gone, so move focus here; screen
  // readers then announce the chat region instead of losing their place.
  useEffect(() => {
    sectionRef.current?.focus()
  }, [])

  return (
    <section ref={sectionRef} className="chat" aria-label={copy.chat.label} tabIndex={-1}>
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
