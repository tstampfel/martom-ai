import type { Dispatch, FormEvent, SetStateAction } from 'react'
import type { ChipQuestion, ChipsEvent } from '../api/events'
import {
  CORNERS_QUESTION_ID,
  formatMetres,
  isComplete,
  resizeSides,
  sideCountFor,
  sidesTotal,
  type ClarifyAnswers,
} from '../clarify'
import { copy } from '../copy'
import { ArrowIcon } from './Icons'

type ClarifyFormProps = {
  chips: ChipsEvent
  /** The reply text so far, shown as the card's heading with the intro. */
  text: string
  answers: ClarifyAnswers
  /** A state setter: updates read the latest answers, so quick taps don't drop one. */
  onChange: Dispatch<SetStateAction<ClarifyAnswers>>
  onSubmit: () => void
  /** False while the reply still streams. */
  canSubmit: boolean
}

/**
 * The advisor's clarify questions as tappable chips (2-clarify.html). Picking
 * the corners sets how many side-length fields show; the customer can add or
 * remove sides. "Prikaži prijedlog" sends the answers once all are given.
 */
export function ClarifyForm({ chips, text, answers, onChange, onSubmit, canSubmit }: ClarifyFormProps) {
  const complete = isComplete(chips, answers)

  function pick(question: ChipQuestion, value: string) {
    onChange((current) => ({
      single: { ...current.single, [question.id]: value },
      sides: question.id === CORNERS_QUESTION_ID ? resizeSides(current.sides, sideCountFor(value)) : current.sides,
    }))
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (complete && canSubmit) onSubmit()
  }

  const heading = [text.trim(), chips.intro].filter(Boolean).join(' ')
  // Only the chip questions are numbered; the side lengths belong to the corners (as in the mockups).
  const singleIds = chips.questions.filter((question) => question.kind === 'single').map((question) => question.id)

  return (
    <form className="clarify" onSubmit={handleSubmit}>
      {heading ? <p className="clarify__heading">{heading}</p> : null}
      {chips.questions.map((question) =>
        question.kind === 'single' ? (
          <SingleQuestion
            key={question.id}
            number={singleIds.indexOf(question.id) + 1}
            question={question}
            picked={answers.single[question.id]}
            onPick={(value) => pick(question, value)}
          />
        ) : (
          <SidesQuestion
            key={question.id}
            question={question}
            sides={answers.sides}
            onChange={(sides) => onChange((current) => ({ ...current, sides }))}
          />
        ),
      )}
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
}: {
  number: number
  question: ChipQuestion
  picked: string | undefined
  onPick: (value: string) => void
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
      {note ? <p className="clarify__note">{note}</p> : null}
    </fieldset>
  )
}

function SidesQuestion({
  question,
  sides,
  onChange,
}: {
  question: ChipQuestion
  sides: string[]
  onChange: (sides: string[]) => void
}) {
  // At least one field, even before the corners are picked.
  const shown = sides.length > 0 ? sides : ['']
  return (
    <fieldset className="clarify__sides">
      <legend className="clarify__label">{question.text}</legend>
      <div className="clarify__side-row">
        {shown.map((side, index) => (
          <label key={index} className="clarify__side">
            {copy.chat.clarify.side(index + 1)}
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={side}
              onChange={(event) => onChange(shown.map((old, i) => (i === index ? event.target.value : old)))}
            />
          </label>
        ))}
        <button type="button" className="opt" onClick={() => onChange([...shown, ''])}>
          {copy.chat.clarify.addSide}
        </button>
        {shown.length > 1 ? (
          <button
            type="button"
            className="opt opt--num"
            aria-label={copy.chat.clarify.removeSide}
            onClick={() => onChange(shown.slice(0, -1))}
          >
            −
          </button>
        ) : null}
        <span className="clarify__total">{copy.chat.clarify.total(formatMetres(sidesTotal(shown)))}</span>
      </div>
    </fieldset>
  )
}
