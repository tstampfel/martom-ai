import type { StepEvent } from '../api/events'
import { copy } from '../copy'

const MARKS: Record<StepEvent['status'], string> = { working: '…', done: '✓', needs_input: '?' }

type StepCardProps = {
  steps: StepEvent[]
  /** The reply is still streaming: show the pulsing "uživo" dot. */
  live: boolean
}

/** The advisor's "Savjetnik radi" steps for one reply, updated in place as they stream. */
export function StepCard({ steps, live }: StepCardProps) {
  return (
    <div className="step-card">
      {live ? (
        <span className="step-card__live">
          <span className="step-card__dot" aria-hidden="true" />
          {copy.chat.steps.live}
        </span>
      ) : null}
      <ul className="step-card__list" aria-label={copy.chat.steps.label}>
        {steps.map((step) => (
          <li key={step.id} className={`step-card__step step-card__step--${step.status}`}>
            <span className="step-card__mark" aria-hidden="true">
              {MARKS[step.status]}
            </span>
            {step.text}
            <span className="visually-hidden"> ({copy.chat.steps.status[step.status]})</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
