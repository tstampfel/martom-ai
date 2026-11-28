import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { copy } from '../copy'
import { usePrefersReducedMotion, useTypingPlaceholder } from '../hooks'
import { AiNotice } from './AiNotice'
import { ArrowIcon } from './Icons'

type QuestionFormProps = {
  onSubmit: (text: string) => void
  /** Focus the box on mount (when the customer comes back from the chat). */
  focusOnMount?: boolean
}

/**
 * The question box with the animated placeholder. It is its own component so
 * the placeholder's ticks (every 22–75 ms) re-render only this form, not the
 * whole start screen.
 */
export function QuestionForm({ onSubmit, focusOnMount = false }: QuestionFormProps) {
  const inputId = useId()
  const noticeId = useId()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [question, setQuestion] = useState('')
  const [focused, setFocused] = useState(false)
  const reducedMotion = usePrefersReducedMotion()
  // Still while focused, so a screen reader never reads a half-typed example.
  const still = reducedMotion || focused
  const { text, example } = useTypingPlaceholder(copy.start.examples, still || question !== '')
  // The trailing bar stands in for a text cursor, as in the mockups.
  const placeholder = reducedMotion ? copy.start.examples[0] : still ? example : `${text}|`

  useEffect(() => {
    if (focusOnMount) inputRef.current?.focus()
  }, [focusOnMount])

  // Grow the box with the question so every line stays visible; CSS caps it
  // with max-height and then scrolls. "auto" first lets it shrink again.
  useLayoutEffect(() => {
    const box = inputRef.current
    if (!box) return
    box.style.height = 'auto'
    if (box.scrollHeight > 0) box.style.height = `${box.scrollHeight}px`
  }, [question])

  function submit() {
    const trimmed = question.trim()
    if (trimmed) onSubmit(trimmed)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  // Enter sends, Shift+Enter adds a new line. The Enter that confirms an IME
  // composition does nothing: `isComposing` covers most browsers, keyCode 229
  // covers Safari, which ends the composition before this keydown.
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const composing = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
    if (event.key === 'Enter' && !event.shiftKey && !composing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="ask" onSubmit={handleSubmit}>
      <label className="ask__label" htmlFor={inputId}>
        {copy.start.inputLabel}
      </label>
      <div className="ask__box">
        <textarea
          ref={inputRef}
          id={inputId}
          className="ask__input"
          rows={1}
          value={question}
          placeholder={placeholder}
          // The AI notice is read with the box, before the first message.
          aria-describedby={noticeId}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <button className="ask__submit" type="submit">
          {copy.start.submit}
          <ArrowIcon />
        </button>
      </div>
      {/* EU AI Act Art. 50 (#151): said before the first message, also to
          screen readers, through the box's aria-describedby. */}
      <AiNotice id={noticeId} text={copy.aiNotice.start} tone="on-brand" />
    </form>
  )
}
