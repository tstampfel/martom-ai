// Pure helpers for the clarify questions (chips event) and for showing
// prices: parsing side lengths, counting sides, and turning the customer's
// answers into the next request and their chat bubble.

import type { ChipsEvent, Price } from './api/events'

/** "53.00" (API) → "53,00 KM" (customer). */
export function formatPrice(price: Price): string {
  return `${price.replace('.', ',')} KM`
}

/** "7,5" or "7.5" → 7.5; null when empty, not a number or not above 0. */
export function parseMetres(text: string): number | null {
  const trimmed = text.trim().replace(',', '.')
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null
  const value = Number(trimmed)
  return value > 0 ? value : null
}

/** 17.5 → "17,5". */
export function formatMetres(value: number): string {
  return String(Math.round(value * 100) / 100).replace('.', ',')
}

/** The id of the corners question; its answer sets how many side fields show. */
export const CORNERS_QUESTION_ID = 'corners'

/**
 * How many side-length fields to show for a corners answer: an open fence
 * has one side more than it has corners ("4+" starts at 5; the customer adds
 * more). Unknown or unanswered corners start with one field.
 */
export function sideCountFor(cornersValue: string | undefined): number {
  const corners = Number.parseInt(cornersValue ?? '', 10)
  return Number.isNaN(corners) ? 1 : corners + 1
}

/** Keeps the typed sides, padding with empty fields or cutting to `count`. */
export function resizeSides(sides: readonly string[], count: number): string[] {
  return Array.from({ length: count }, (_, index) => sides[index] ?? '')
}

export function sidesTotal(sides: readonly string[]): number {
  return sides.reduce((sum, side) => sum + (parseMetres(side) ?? 0), 0)
}

export type ClarifyAnswers = {
  /** Question id → picked option value, for `single` questions. */
  single: Record<string, string>
  /** One entry per side, as typed, for the `side_lengths` question. */
  sides: string[]
}

/** True when every question has an answer and every side a valid length. */
export function isComplete(chips: ChipsEvent, answers: ClarifyAnswers): boolean {
  return chips.questions.every((question) =>
    question.kind === 'single'
      ? question.id in answers.single
      : answers.sides.length > 0 && answers.sides.every((side) => parseMetres(side) !== null),
  )
}

function sidesText(sides: readonly string[]): string {
  return sides.map((side) => side.trim()).join(' + ')
}

/** Question id → value for the next request (see ChatRequest.answers). */
export function answersForRequest(chips: ChipsEvent, answers: ClarifyAnswers): Record<string, string> {
  const out: Record<string, string> = {}
  for (const question of chips.questions) {
    const value = question.kind === 'single' ? answers.single[question.id] : sidesText(answers.sides)
    if (value !== undefined) out[question.id] = value
  }
  return out
}

/** The customer's bubble: the picked labels and the sides, in question order. */
export function answerSummary(chips: ChipsEvent, answers: ClarifyAnswers): string {
  return chips.questions
    .map((question) => {
      if (question.kind === 'side_lengths') return `${sidesText(answers.sides)} m`
      return question.options.find((option) => option.value === answers.single[question.id])?.label
    })
    .filter((part) => part !== undefined)
    .join(' · ')
}
