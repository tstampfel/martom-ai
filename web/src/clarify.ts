// Pure helpers for the clarify questions (chips event) and for showing
// prices: parsing side lengths, counting sides, and turning the customer's
// answers into the next request and their chat bubble.

import type { ChipsEvent, Price } from './api/events'
import { copy } from './copy'

/** "53.00" (API) → "53,00", for columns that say KM elsewhere. */
export function formatAmount(price: Price): string {
  return price.replace('.', ',')
}

/** "53.00" (API) → "53,00 KM" (customer). */
export function formatPrice(price: Price): string {
  return `${formatAmount(price)} KM`
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

/** The question whose answer sets how many side fields show. */
export const CORNERS_QUESTION_ID = 'corners'
/**
 * The closed-loop flag, sent as its own chip question with the values below
 * (no contract change); the form shows it as the "Ide okolo" toggle.
 */
export const CLOSED_QUESTION_ID = 'closed'
const CLOSED_YES = 'yes'
const CLOSED_NO = 'no'

export type ClarifyAnswers = {
  /** Question id → picked option value, for the chip questions (not the closed flag). */
  single: Record<string, string>
  /**
   * The number of corners: from the picked option ("4+" starts at 4), raised by
   * "+ strana". null while unanswered or "Ne znam".
   */
  corners: number | null
  /** The fence goes all the way round: as many sides as corners. */
  closed: boolean
  /** One entry per side field, as typed. Empty when no side lengths are asked. */
  sides: string[]
}

function hasQuestion(chips: ChipsEvent, id: string): boolean {
  return chips.questions.some((question) => question.id === id)
}

function hasSidesQuestion(chips: ChipsEvent): boolean {
  return chips.questions.some((question) => question.kind === 'side_lengths')
}

/**
 * How many side fields to show (docs/mockups/{web,mobile}/2-clarify.html): none for 0 or
 * unknown corners (the length comes from the message), one per corner for a
 * closed fence, else one more than the corners.
 */
export function sideCount(corners: number | null, closed: boolean): number {
  if (corners === null || corners === 0) return 0
  return closed ? corners : corners + 1
}

/** Keeps the typed sides, padding with empty fields or cutting to `count`. */
export function resizeSides(sides: readonly string[], count: number): string[] {
  return Array.from({ length: count }, (_, index) => sides[index] ?? '')
}

export function sidesTotal(sides: readonly string[]): number {
  return sides.reduce((sum, side) => sum + (parseMetres(side) ?? 0), 0)
}

/** Nothing picked. Without a corners question the side lengths start with one field. */
export function initialAnswers(chips: ChipsEvent): ClarifyAnswers {
  const sides = hasSidesQuestion(chips) && !hasQuestion(chips, CORNERS_QUESTION_ID) ? [''] : []
  return { single: {}, corners: null, closed: false, sides }
}

/** "2" → 2, "4+" → 4, "unknown" → null. */
function cornersFromValue(value: string): number | null {
  const corners = Number.parseInt(value, 10)
  return Number.isNaN(corners) ? null : corners
}

export function pickOption(answers: ClarifyAnswers, questionId: string, value: string): ClarifyAnswers {
  return { ...answers, single: { ...answers.single, [questionId]: value } }
}

/** Picks a corners option; a closed fence needs at least 3 corners. */
export function pickCorners(chips: ChipsEvent, answers: ClarifyAnswers, value: string): ClarifyAnswers {
  const corners = cornersFromValue(value)
  const closed = corners !== null && corners >= 3 ? answers.closed : false
  const sides = hasSidesQuestion(chips) ? resizeSides(answers.sides, sideCount(corners, closed)) : []
  return { ...pickOption(answers, CORNERS_QUESTION_ID, value), corners, closed, sides }
}

/**
 * Turns "Ide okolo (zatvoreno)" on or off. Closing a fence with fewer than 3
 * (or unknown) corners picks the 4-corner option, as "around the yard"
 * usually means 4.
 */
export function toggleClosed(chips: ChipsEvent, answers: ClarifyAnswers): ClarifyAnswers {
  const closed = !answers.closed
  let next = answers
  if (closed && (answers.corners === null || answers.corners < 3)) {
    const four = chips.questions
      .find((question) => question.id === CORNERS_QUESTION_ID)
      ?.options.find((option) => cornersFromValue(option.value) === 4)
    next = { ...pickOption(answers, CORNERS_QUESTION_ID, four?.value ?? '4'), corners: 4 }
  }
  const sides = hasSidesQuestion(chips) ? resizeSides(next.sides, sideCount(next.corners, closed)) : []
  return { ...next, closed, sides }
}

/** "+ strana" shows from 4 corners on, or always when there is no corners question. */
export function canAddSide(chips: ChipsEvent, answers: ClarifyAnswers): boolean {
  if (!hasQuestion(chips, CORNERS_QUESTION_ID)) return true
  return answers.corners !== null && answers.corners >= 4
}

/** Adds a side field; with a corners question the corners go up with it, so both stay consistent. */
export function addSide(chips: ChipsEvent, answers: ClarifyAnswers): ClarifyAnswers {
  if (!hasQuestion(chips, CORNERS_QUESTION_ID) || answers.corners === null) {
    return { ...answers, sides: [...answers.sides, ''] }
  }
  return { ...answers, corners: answers.corners + 1, sides: [...answers.sides, ''] }
}

/** True when every chip question has an answer and every side field a valid length. */
export function isComplete(chips: ChipsEvent, answers: ClarifyAnswers): boolean {
  return chips.questions.every((question) => {
    if (question.id === CLOSED_QUESTION_ID) return true
    if (question.kind === 'side_lengths') return answers.sides.every((side) => parseMetres(side) !== null)
    return question.id in answers.single
  })
}

function sidesText(sides: readonly string[]): string {
  return sides.map((side) => side.trim()).join(' + ')
}

/**
 * Question id → value for the next request (see ChatRequest.answers): the
 * corners as the actual count when known, the closed flag as "yes"/"no",
 * the sides joined (left out when none were asked).
 */
export function answersForRequest(chips: ChipsEvent, answers: ClarifyAnswers): Record<string, string> {
  const out: Record<string, string> = {}
  for (const question of chips.questions) {
    if (question.id === CLOSED_QUESTION_ID) {
      out[question.id] = answers.closed ? CLOSED_YES : CLOSED_NO
    } else if (question.kind === 'side_lengths') {
      if (answers.sides.length > 0) out[question.id] = sidesText(answers.sides)
    } else if (question.id === CORNERS_QUESTION_ID && answers.corners !== null) {
      out[question.id] = String(answers.corners)
    } else if (question.id in answers.single) {
      out[question.id] = answers.single[question.id]
    }
  }
  return out
}

/** "2 kuta", "4 kuta, zatvoreno", "Kutovi: ne znam"; null before the corners are picked. */
export function cornersText(answers: ClarifyAnswers): string | null {
  const { clarify } = copy.chat
  if (answers.corners === null) {
    return CORNERS_QUESTION_ID in answers.single ? clarify.cornersUnknown : null
  }
  const text = clarify.corners(answers.corners)
  return answers.closed ? `${text}${clarify.closedSuffix}` : text
}

/** The customer's bubble: the corners, the sides and the picked labels, in question order. */
export function answerSummary(chips: ChipsEvent, answers: ClarifyAnswers): string {
  return chips.questions
    .map((question) => {
      if (question.id === CLOSED_QUESTION_ID) return null
      if (question.id === CORNERS_QUESTION_ID) return cornersText(answers)
      if (question.kind === 'side_lengths') return answers.sides.length > 0 ? `${sidesText(answers.sides)} m` : null
      return question.options.find((option) => option.value === answers.single[question.id])?.label ?? null
    })
    .filter((part) => part !== null)
    .join(' · ')
}
