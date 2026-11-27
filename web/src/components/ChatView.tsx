import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type { BomRecalculator } from '../api/bomRecalculator'
import type { ChatClient, RequestAnswers } from '../api/chatClient'
import type { ChatError, Turn } from '../chat'
import { answerSummary, initialAnswers, requestAnswers, type ClarifyAnswers } from '../clarify'
import { copy } from '../copy'
import { useMediaQuery } from '../hooks'
import { useBomEditor } from '../useBomEditor'
import { useChat } from '../useChat'
import { BomCard } from './BomCard'
import { ClarifyForm, type UpdateAnswers } from './ClarifyForm'
import { ArrowIcon } from './Icons'
import { ProductCards } from './ProductCards'
import { ProjectPanel } from './ProjectPanel'
import { StepCard } from './StepCard'
import './ChatView.css'

type ChatViewProps = {
  /** The message that opened the chat (question box, chip or header category). */
  firstMessage: string
  client: ChatClient
  /** Where a quantity edit on the materials list is recalculated. */
  recalculator: BomRecalculator
  onBack: () => void
}

/** Wide screens: the layout of docs/mockups/web/ (ChatView.css switches at 767 px). */
const WIDE_QUERY = '(min-width: 768px)'

/**
 * The conversation (2-clarify.html, 3-results.html): the customer's
 * messages, and for each reply its steps, streaming text, clarify chips,
 * product cards, materials list or error. On wide screens the "Vaš projekt"
 * panel sits beside open clarify questions, and otherwise the materials list
 * sits there as the charcoal panel; on phones the list is a card in the reply.
 */
export function ChatView({ firstMessage, client, recalculator, onBack }: ChatViewProps) {
  const { state, send } = useChat(client, firstMessage)
  const wide = useMediaQuery(WIDE_QUERY)
  // Answers to the newest reply's chips; null until the customer touches
  // them, and again after every message, so new chips start afresh.
  const [answersState, setAnswersState] = useState<ClarifyAnswers | null>(null)
  const sectionRef = useRef<HTMLElement>(null)

  // The control that opened the chat is gone, so move focus here; screen
  // readers then announce the chat region instead of losing their place.
  useEffect(() => {
    sectionRef.current?.focus()
  }, [])

  const lastIndex = state.turns.length - 1
  const lastReply = state.turns[lastIndex].reply
  const openChips = lastReply.error ? null : lastReply.chips
  const busy = lastReply.streaming
  const answers = answersState ?? (openChips ? initialAnswers(openChips) : null)

  // One materials list: the newest bom event. Its edits live here, so they
  // survive moving between the panel and the card when the window resizes.
  const bomTurn = state.turns.findLastIndex((turn) => turn.reply.bom !== null)
  const bomEditor = useBomEditor(bomTurn === -1 ? null : state.turns[bomTurn].reply.bom, recalculator)
  const bomInPanel = wide && !openChips
  const bomCard = (variant: 'card' | 'panel') =>
    bomEditor ? (
      <BomCard
        bom={bomEditor.bom}
        quantities={bomEditor.quantities}
        pending={bomEditor.pending}
        failed={bomEditor.failed}
        variant={variant}
        onQuantityChange={bomEditor.setQuantity}
        // The cart is #27; until then the button has nothing to fill.
        onAddAll={() => {}}
      />
    ) : null

  const updateAnswers: UpdateAnswers = (update) => {
    if (!openChips) return
    const chips = openChips
    setAnswersState((current) => update(current ?? initialAnswers(chips)))
  }

  function sendMessage(text: string, answersBody: RequestAnswers | null = null) {
    setAnswersState(null)
    send(text, answersBody)
  }

  function submitAnswers() {
    if (!openChips || !answers) return
    sendMessage(answerSummary(openChips, answers), requestAnswers(openChips, answers))
  }

  return (
    <div className="chat">
      <section ref={sectionRef} className="chat__conversation" aria-label={copy.chat.label} tabIndex={-1}>
        {state.turns.map((turn, index) => (
          <TurnView
            key={index}
            turn={turn}
            bom={index === bomTurn && !bomInPanel ? bomCard('card') : null}
            clarify={
              index === lastIndex && openChips && answers
                ? { answers, onChange: updateAnswers, onSubmit: submitAnswers, canSubmit: !busy }
                : null
            }
          />
        ))}
        <Composer busy={busy} clarifying={openChips !== null} onSend={(text) => sendMessage(text)} />
        <button type="button" className="chat__back" onClick={onBack}>
          {copy.chat.back}
        </button>
      </section>
      {openChips && answers ? (
        <ProjectPanel fallbackTitle={state.turns[0].text} chips={openChips} answers={answers} />
      ) : null}
      {bomInPanel ? bomCard('panel') : null}
    </div>
  )
}

type ClarifyControls = {
  answers: ClarifyAnswers
  onChange: UpdateAnswers
  onSubmit: () => void
  canSubmit: boolean
}

/** One customer message and the advisor's reply to it. */
function TurnView({ turn, bom, clarify }: { turn: Turn; bom: ReactNode; clarify: ClarifyControls | null }) {
  const { reply } = turn
  const waiting = reply.streaming && !reply.text && !reply.chips && reply.cards.length === 0 && !reply.error
  // Old chips are no longer answerable; their question text stays as plain text.
  const plainText = clarify && reply.chips ? '' : [reply.text.trim(), reply.chips?.intro].filter(Boolean).join(' ')

  return (
    <>
      <p className="chat__bubble">
        <span className="visually-hidden">{copy.chat.you}: </span>
        {turn.text}
      </p>
      <div className="chat__reply" aria-busy={reply.streaming}>
        <span className="visually-hidden">{copy.chat.advisor}:</span>
        {reply.steps.length > 0 ? <StepCard steps={reply.steps} live={reply.streaming} /> : null}
        {waiting ? (
          <output className="chat__writing">{copy.chat.writing}</output>
        ) : null}
        {clarify && reply.chips ? (
          <ClarifyForm chips={reply.chips} text={reply.text} {...clarify} />
        ) : plainText ? (
          <p className="chat__text">{plainText}</p>
        ) : null}
        {reply.cards.length > 0 ? <ProductCards cards={reply.cards} /> : null}
        {bom}
        {reply.error ? <ReplyError error={reply.error} /> : null}
      </div>
    </>
  )
}

function errorText({ kind, retryAfterSeconds }: ChatError): string {
  const { errors } = copy.chat
  return kind === 'rate_limited' ? errors.rate_limited(retryAfterSeconds) : errors[kind]
}

function ReplyError({ error }: { error: ChatError }) {
  return (
    <p className="chat__error" role="alert">
      {errorText(error)}
    </p>
  )
}

/** The message box under the conversation. Sending waits until the reply has finished. */
function Composer({
  busy,
  clarifying,
  onSend,
}: {
  busy: boolean
  clarifying: boolean
  onSend: (text: string) => void
}) {
  const inputId = useId()
  const [text, setText] = useState('')
  const { composer } = copy.chat

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed || busy) return
    onSend(trimmed)
    setText('')
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <label className="visually-hidden" htmlFor={inputId}>
        {composer.label}
      </label>
      <input
        id={inputId}
        className="composer__input"
        type="text"
        autoComplete="off"
        value={text}
        placeholder={clarifying ? composer.placeholderClarify : composer.placeholder}
        onChange={(event) => setText(event.target.value)}
      />
      <button type="submit" className="composer__submit" disabled={busy} aria-label={composer.submit}>
        <span className="composer__submit-text">{composer.submit}</span>
        <ArrowIcon />
      </button>
    </form>
  )
}
