import type { ChipsEvent } from '../api/events'
import { formatMetres, sidesTotal, type ClarifyAnswers } from '../clarify'
import { copy } from '../copy'

type ProjectPanelProps = {
  /** The customer's first message, as the project's title. */
  title: string
  chips: ChipsEvent
  answers: ClarifyAnswers
}

/**
 * The dark "Vaš projekt" panel next to the clarify questions on wide screens
 * (web/2-clarify.html). It fills in as the customer answers: the length from
 * the sides, then each question with the picked option.
 */
export function ProjectPanel({ title, chips, answers }: ProjectPanelProps) {
  const total = sidesTotal(answers.sides)
  const hasSides = chips.questions.some((question) => question.kind === 'side_lengths')
  const { project } = copy.chat

  return (
    <aside className="project-panel" aria-label={project.label}>
      <span className="project-panel__eyebrow">{project.label}</span>
      <span className="project-panel__title">{title}</span>
      <dl className="project-panel__rows">
        {hasSides ? (
          <div className="project-panel__row">
            <dt>{project.length}</dt>
            <dd>{total > 0 ? `${formatMetres(total)} m` : project.unanswered}</dd>
          </div>
        ) : null}
        {chips.questions
          .filter((question) => question.kind === 'single')
          .map((question) => (
            <div key={question.id} className="project-panel__row">
              <dt>{question.text}</dt>
              <dd>
                {question.options.find((option) => option.value === answers.single[question.id])?.label ??
                  project.unanswered}
              </dd>
            </div>
          ))}
      </dl>
      <p className="project-panel__hint">{project.hint}</p>
    </aside>
  )
}
