import type { FormEvent } from 'react'
import type { ChipQuestion, ChipsEvent } from '../api/events'
import {
  addSide,
  canAddSide,
  CORNER_OPTIONS,
  formatMetres,
  hasSideTooLong,
  isComplete,
  pickCorners,
  pickOption,
  sidesTotal,
  toggleClosed,
  type ClarifyAnswers,
  type CornerOption,
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
 * The advisor's clarify questions as tappable chips (2-clarify.html). The
 * layout question shows the corner chips, the "Ide okolo (zatvoreno)" toggle
 * and the side fields (none for "Ne znam", none for 0 corners when the length
 * came from the message), and "+ strana" (from 4 corners) raises the corners
 * with each side. Options the advisor sent as `default` start picked.
 * "Prikaži prijedlog" sends the answers once all are given.
 */
export function ClarifyForm({ chips, text, answers, onChange, onSubmit, canSubmit }: ClarifyFormProps) {
  const complete = isComplete(chips, answers)
  const tooLong = hasSideTooLong(answers)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (complete && canSubmit) onSubmit()
  }

  const heading = [text.trim(), chips.intro].filter(Boolean).join(' ')

  return (
    <form className="clarify" onSubmit={handleSubmit}>
      {heading ? <p className="clarify__heading">{heading}</p> : null}
      {chips.questions.map((question, index) => {
        if (question.kind === 'layout') {
          return (
            <LayoutQuestion
              key={question.id}
              number={index + 1}
              question={question}
              answers={answers}
              onPick={(option) => onChange((current) => pickCorners(chips, current, option))}
              onToggleClosed={() => onChange((current) => toggleClosed(chips, current))}
              onSides={(sides) => onChange((current) => ({ ...current, sides }))}
              onAddSide={() => onChange((current) => addSide(current))}
            />
          )
        }
        return (
          <SingleQuestion
            key={question.id}
            number={index + 1}
            question={question}
            picked={answers.single[question.id]}
            onPick={(value) => onChange((current) => pickOption(current, question.id, value))}
          />
        )
      })}
      <button type="submit" className="clarify__submit" disabled={!complete || !canSubmit}>
        {copy.chat.clarify.submit}
        <ArrowIcon />
      </button>
      {tooLong ? (
        <p className="clarify__hint">{copy.chat.clarify.sideTooLong}</p>
      ) : complete ? null : (
        <p className="clarify__hint">{copy.chat.clarify.incomplete}</p>
      )}
    </form>
  )
}

function optionClass(label: string, on: boolean, twoLines = false): string {
  return ['opt', label.length <= 2 ? 'opt--num' : '', twoLines ? 'opt--two' : '', on ? 'opt--on' : '']
    .filter(Boolean)
    .join(' ')
}

function LayoutQuestion({
  number,
  question,
  answers,
  onPick,
  onToggleClosed,
  onSides,
  onAddSide,
}: {
  number: number
  question: ChipQuestion
  answers: ClarifyAnswers
  onPick: (option: CornerOption) => void
  onToggleClosed: () => void
  onSides: (sides: string[]) => void
  onAddSide: () => void
}) {
  const { clarify } = copy.chat
  return (
    <fieldset className="clarify__question">
      <legend className="clarify__label">
        <span aria-hidden="true">{number}. </span>
        {question.text}
      </legend>
      <div className="clarify__options">
        {CORNER_OPTIONS.map((option) => {
          const label = option === 'unknown' ? clarify.cornerUnknown : option
          const on = option === answers.cornerOption
          return (
            <button
              key={option}
              type="button"
              className={optionClass(label, on)}
              aria-pressed={on}
              onClick={() => onPick(option)}
            >
              {label}
            </button>
          )
        })}
      </div>
      <button
        type="button"
        className={answers.closed ? 'opt opt--on clarify__closed' : 'opt clarify__closed'}
        aria-pressed={answers.closed}
        onClick={onToggleClosed}
      >
        {clarify.closedToggle}
      </button>
      {answers.sides.length > 0 ? (
        <SidesFields sides={answers.sides} canAdd={canAddSide(answers)} onChange={onSides} onAdd={onAddSide} />
      ) : null}
    </fieldset>
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
            className={optionClass(option.label, option.value === picked, option.badge !== null)}
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

function SidesFields({
  sides,
  canAdd,
  onChange,
  onAdd,
}: {
  sides: string[]
  canAdd: boolean
  onChange: (sides: string[]) => void
  onAdd: () => void
}) {
  return (
    <fieldset className="clarify__sides">
      <legend className="clarify__label">{copy.chat.clarify.sidesLabel}</legend>
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
