import type { ChipQuestion, ChipsEvent } from '../api/events'
import {
  CLOSED_QUESTION_ID,
  CORNERS_QUESTION_ID,
  cornersText,
  formatMetres,
  sidesTotal,
  type ClarifyAnswers,
} from '../clarify'
import { copy } from '../copy'

type ProjectPanelProps = {
  /** The customer's first message, as the project's title. */
  title: string
  chips: ChipsEvent
  answers: ClarifyAnswers
}

/**
 * The dark "Vaš projekt" panel next to the clarify questions on wide screens
 * (web/2-clarify.html). It fills in as the customer answers: the length and
 * the sides, the corners (with "zatvoreno"), then each chip question. Labels
 * are the question texts, as the contract has no short labels, title or height.
 */
export function ProjectPanel({ title, chips, answers }: ProjectPanelProps) {
  const total = sidesTotal(answers.sides)
  const hasSides = chips.questions.some((question) => question.kind === 'side_lengths')
  const { project } = copy.chat

  function answerLabel(question: ChipQuestion): string {
    // A known count reads "2 kuta" or "4 kuta, zatvoreno"; "Ne znam" shows as its label.
    if (question.id === CORNERS_QUESTION_ID && answers.corners !== null) return cornersText(answers) ?? project.unanswered
    return question.options.find((option) => option.value === answers.single[question.id])?.label ?? project.unanswered
  }

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
        {answers.sides.length > 0 ? (
          <div className="project-panel__row">
            <dt>{project.sides}</dt>
            <dd>{`${answers.sides.map((side) => side.trim() || '?').join(' + ')} m`}</dd>
          </div>
        ) : null}
        {chips.questions
          .filter((question) => question.kind === 'single' && question.id !== CLOSED_QUESTION_ID)
          .map((question) => (
            <div key={question.id} className="project-panel__row">
              <dt>{question.text}</dt>
              <dd>{answerLabel(question)}</dd>
            </div>
          ))}
      </dl>
      <p className="project-panel__hint">{project.hint}</p>
    </aside>
  )
}
