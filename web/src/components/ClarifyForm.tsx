import type { FormEvent, ReactNode } from 'react'
import type { ChipQuestion, ChipsEvent } from '../api/events'
import {
  addSide,
  canAddSide,
  CLOSED_QUESTION_ID,
  CORNERS_QUESTION_ID,
  formatMetres,
  isComplete,
  pickCorners,
  pickOption,
  sidesTotal,
  toggleClosed,
  type ClarifyAnswers,
} from '../clarify'
import { copy } from '../copy'
import { ArrowIcon } from './Icons'

/** Updates the answers from the latest ones, so quick taps in one render don't drop an answer. */
export type UpdateAnswers = (update: (current: ClarifyAnswers) => ClarifyAnswers) => void

type ClarifyFormProps = {
  chips: ChipsEvent
  /** The reply text so far, shown as the card's heading with the intro. */
  text: string
  answers: ClarifyAnswers
  onChange: UpdateAnswers
  onSubmit: () => void
  /** False while the reply still streams. */
  canSubmit: boolean
}

/**
 * The advisor's clarify questions as tappable chips (2-clarify.html). As in
 * the mockups, the corners answer sets the side fields (none for 0 or "Ne
 * znam"), a question with id "closed" shows as the "Ide okolo (zatvoreno)"
 * toggle under the corners, and "+ strana" (from 4 corners) raises the
 * corners with each side. "Prikaži prijedlog" sends the answers once all are given.
 */
export function ClarifyForm({ chips, text, answers, onChange, onSubmit, canSubmit }: ClarifyFormProps) {
  const complete = isComplete(chips, answers)
  const hasClosedToggle = chips.questions.some((question) => question.id === CLOSED_QUESTION_ID)

  function pick(question: ChipQuestion, value: string) {
    onChange((current) =>
      question.id === CORNERS_QUESTION_ID ? pickCorners(chips, current, value) : pickOption(current, question.id, value),
    )
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (complete && canSubmit) onSubmit()
  }

  const heading = [text.trim(), chips.intro].filter(Boolean).join(' ')
  // Numbered like the mockups: the chip questions only (the side lengths and
  // the closed toggle belong to the corners).
  const numbered = chips.questions
    .filter((question) => question.kind === 'single' && question.id !== CLOSED_QUESTION_ID)
    .map((question) => question.id)

  return (
    <form className="clarify" onSubmit={handleSubmit}>
      {heading ? <p className="clarify__heading">{heading}</p> : null}
      {chips.questions.map((question) => {
        if (question.id === CLOSED_QUESTION_ID) return null
        if (question.kind === 'side_lengths') {
          return answers.sides.length > 0 ? (
            <SidesQuestion
              key={question.id}
              question={question}
              sides={answers.sides}
              canAdd={canAddSide(chips, answers)}
              onChange={(sides) => onChange((current) => ({ ...current, sides }))}
              onAdd={() => onChange((current) => addSide(chips, current))}
            />
          ) : null
        }
        return (
          <SingleQuestion
            key={question.id}
            number={numbered.indexOf(question.id) + 1}
            question={question}
            picked={answers.single[question.id]}
            onPick={(value) => pick(question, value)}
          >
            {question.id === CORNERS_QUESTION_ID && hasClosedToggle ? (
              <button
                type="button"
                className={answers.closed ? 'opt opt--on clarify__closed' : 'opt clarify__closed'}
                aria-pressed={answers.closed}
                onClick={() => onChange((current) => toggleClosed(chips, current))}
              >
                {copy.chat.clarify.closedToggle}
              </button>
            ) : null}
          </SingleQuestion>
        )
      })}
      <button type="submit" className="clarify__submit" disabled={!complete || !canSubmit}>
        {copy.chat.clarify.submit}
        <ArrowIcon />
      </button>
      {complete ? null : <p className="clarify__hint">{copy.chat.clarify.incomplete}</p>}
    </form>
  )
}

function SingleQuestion({
  number,
  question,
  picked,
  onPick,
  children,
}: {
  number: number
  question: ChipQuestion
  picked: string | undefined
  onPick: (value: string) => void
  /** Extra controls under the options (the closed toggle). */
  children?: ReactNode
}) {
  const note = question.options.find((option) => option.value === picked)?.note
  return (
    <fieldset className="clarify__question">
      <legend className="clarify__label">
        <span aria-hidden="true">{number}. </span>
        {question.text}
      </legend>
      <div className="clarify__options">
        {question.options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={[
              'opt',
              option.label.length <= 2 ? 'opt--num' : '',
              option.badge ? 'opt--two' : '',
              option.value === picked ? 'opt--on' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            aria-pressed={option.value === picked}
            onClick={() => onPick(option.value)}
          >
            {option.label}
            {option.badge ? <span className="opt__badge">{option.badge}</span> : null}
          </button>
        ))}
      </div>
      {children}
      {note ? <p className="clarify__note">{note}</p> : null}
    </fieldset>
  )
}

function SidesQuestion({
  question,
  sides,
  canAdd,
  onChange,
  onAdd,
}: {
  question: ChipQuestion
  sides: string[]
  canAdd: boolean
  onChange: (sides: string[]) => void
  onAdd: () => void
}) {
  return (
    <fieldset className="clarify__sides">
      <legend className="clarify__label">{question.text}</legend>
      <div className="clarify__side-row">
        {sides.map((side, index) => (
          <label key={index} className="clarify__side">
            {copy.chat.clarify.side(index + 1)}
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={side}
              onChange={(event) => onChange(sides.map((old, i) => (i === index ? event.target.value : old)))}
            />
          </label>
        ))}
        {canAdd ? (
          <button type="button" className="opt" onClick={onAdd}>
            {copy.chat.clarify.addSide}
          </button>
        ) : null}
        <span className="clarify__total">{copy.chat.clarify.total(formatMetres(sidesTotal(sides)))}</span>
      </div>
    </fieldset>
  )
}
