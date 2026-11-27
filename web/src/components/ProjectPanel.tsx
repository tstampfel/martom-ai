import type { ChipQuestion, ChipsEvent } from '../api/events'
import { formatMetres, panelCornersText, sidesTotal, type ClarifyAnswers } from '../clarify'
import { copy } from '../copy'

type ProjectPanelProps = {
  /** The title when the chips event has no project: the customer's first message. */
  fallbackTitle: string
  chips: ChipsEvent
  answers: ClarifyAnswers
}

/**
 * The dark "Vaš projekt" panel next to the clarify questions on wide screens
 * (web/2-clarify.html). It starts with what the advisor sent in
 * `chips.project` (title, length, facts such as the height) and the default
 * options, and fills in as the customer answers: the length, the corners
 * (with "zatvoreno"), the sides, the facts, then each chip question under its
 * short label.
 */
export function ProjectPanel({ fallbackTitle, chips, answers }: ProjectPanelProps) {
  const { project } = copy.chat
  const summary = chips.project
  const layout = chips.questions.find((question) => question.kind === 'layout')
  const typed = sidesTotal(answers.sides)
  const length = typed > 0 ? typed : (summary?.length_m ?? null)

  function answerLabel(question: ChipQuestion): string {
    const option = question.options.find((candidate) => candidate.value === answers.single[question.id])
    return option ? (option.short_label ?? option.label) : project.unanswered
  }

  return (
    <aside className="project-panel" aria-label={project.label}>
      <span className="project-panel__eyebrow">{project.label}</span>
      <span className="project-panel__title">{summary?.title ?? fallbackTitle}</span>
      <dl className="project-panel__rows">
        {layout || length !== null ? (
          <Row label={project.length} value={length !== null ? `${formatMetres(length)} m` : project.unanswered} />
        ) : null}
        {layout ? (
          <Row label={layout.label ?? layout.text} value={panelCornersText(answers) ?? project.unanswered} />
        ) : null}
        {answers.sides.length > 0 ? (
          <Row label={project.sides} value={`${answers.sides.map((side) => side.trim() || '?').join(' + ')} m`} />
        ) : null}
        {summary?.facts.map((fact) => <Row key={fact.label} label={fact.label} value={fact.value} />)}
        {chips.questions
          .filter((question) => question.kind === 'single')
          .map((question) => (
            <Row key={question.id} label={question.label ?? question.text} value={answerLabel(question)} />
          ))}
      </dl>
      <p className="project-panel__hint">{project.hint}</p>
    </aside>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="project-panel__row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
