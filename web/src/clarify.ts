// Pure helpers for the clarify questions (chips event) and for showing
// prices: parsing side lengths, counting sides, and turning the customer's
// answers into the next request and their chat bubble.

import { MAX_SIDE_M, type RequestAnswers } from './api/chatClient'
import type { ChipQuestion, ChipsEvent, Price } from './api/events'
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

/** The corner chips the layout question shows (2-clarify.html); "4+" starts at 4. */
export const CORNER_OPTIONS = ['0', '1', '2', '3', '4+', 'unknown'] as const
export type CornerOption = (typeof CORNER_OPTIONS)[number]

export type ClarifyAnswers = {
  /** Question id → picked option value, for the single questions. */
  single: Record<string, string>
  /** The picked corner chip; null while unanswered. */
  cornerOption: CornerOption | null
  /**
   * The number of corners: from the picked chip, raised by "+ strana". null
   * while unanswered or "Ne znam".
   */
  corners: number | null
  /** The fence goes all the way round: as many sides as corners. */
  closed: boolean
  /** One entry per side field, as typed. Empty when no side lengths are asked. */
  sides: string[]
}

export function layoutQuestion(chips: ChipsEvent): ChipQuestion | undefined {
  return chips.questions.find((question) => question.kind === 'layout')
}

/** The total length the advisor understood from the message, if any. */
function knownLength(chips: ChipsEvent): number | null {
  return chips.project?.length_m ?? null
}

/**
 * How many side fields to show (docs/mockups/{web,mobile}/2-clarify.html):
 * none for unknown corners, none for 0 corners when the length is known from
 * the message (else one), one per corner for a closed fence, else one more
 * than the corners.
 */
export function sideCount(corners: number | null, closed: boolean, lengthKnown: boolean): number {
  if (corners === null) return 0
  if (corners === 0) return lengthKnown ? 0 : 1
  return closed ? corners : corners + 1
}

/** Keeps the typed sides, padding with empty fields or cutting to `count`. */
export function resizeSides(sides: readonly string[], count: number): string[] {
  return Array.from({ length: count }, (_, index) => sides[index] ?? '')
}

export function sidesTotal(sides: readonly string[]): number {
  return sides.reduce((sum, side) => sum + (parseMetres(side) ?? 0), 0)
}

/** A side the request accepts: a number above 0 and at most MAX_SIDE_M. */
function validSide(text: string): number | null {
  const metres = parseMetres(text)
  return metres !== null && metres <= MAX_SIDE_M ? metres : null
}

/**
 * The one side a straight fence sends without a side field: the length from
 * the message (0 corners, no field shown). null otherwise.
 */
function straightLength(chips: ChipsEvent, answers: ClarifyAnswers): number | null {
  return answers.corners === 0 && answers.sides.length === 0 ? knownLength(chips) : null
}

/**
 * True when a side is longer than MAX_SIDE_M (no BOM for it): a typed side,
 * or the length from the message sent as the one side of a straight fence.
 */
export function hasSideTooLong(chips: ChipsEvent, answers: ClarifyAnswers): boolean {
  const straight = straightLength(chips, answers)
  if (straight !== null && straight > MAX_SIDE_M) return true
  return answers.sides.some((side) => (parseMetres(side) ?? 0) > MAX_SIDE_M)
}

/** Nothing tapped yet; the options the advisor sent as `default` start picked. */
export function initialAnswers(chips: ChipsEvent): ClarifyAnswers {
  const single: Record<string, string> = {}
  for (const question of chips.questions) {
    if (question.kind === 'single' && question.default !== null) single[question.id] = question.default
  }
  return { single, cornerOption: null, corners: null, closed: false, sides: [] }
}

/** "2" → 2, "4+" → 4, "unknown" → null. */
function cornersFromOption(option: CornerOption): number | null {
  return option === 'unknown' ? null : Number.parseInt(option, 10)
}

export function pickOption(answers: ClarifyAnswers, questionId: string, value: string): ClarifyAnswers {
  return { ...answers, single: { ...answers.single, [questionId]: value } }
}

/** Picks a corner chip; a closed fence needs at least 3 corners. */
export function pickCorners(chips: ChipsEvent, answers: ClarifyAnswers, option: CornerOption): ClarifyAnswers {
  const corners = cornersFromOption(option)
  const closed = corners !== null && corners >= 3 ? answers.closed : false
  const sides = resizeSides(answers.sides, sideCount(corners, closed, knownLength(chips) !== null))
  return { ...answers, cornerOption: option, corners, closed, sides }
}

/**
 * Turns "Ide okolo (zatvoreno)" on or off. Closing a fence with fewer than 3
 * (or unknown) corners picks "4+", as "around the yard" usually means 4.
 */
export function toggleClosed(chips: ChipsEvent, answers: ClarifyAnswers): ClarifyAnswers {
  const closed = !answers.closed
  let next = answers
  if (closed && (answers.corners === null || answers.corners < 3)) {
    next = { ...answers, cornerOption: '4+', corners: 4 }
  }
  const sides = resizeSides(next.sides, sideCount(next.corners, closed, knownLength(chips) !== null))
  return { ...next, closed, sides }
}

/** "+ strana" shows from 4 corners on. */
export function canAddSide(answers: ClarifyAnswers): boolean {
  return answers.corners !== null && answers.corners >= 4
}

/** Adds a side field and raises the corners with it, so both stay consistent. */
export function addSide(answers: ClarifyAnswers): ClarifyAnswers {
  if (answers.corners === null) return answers
  return { ...answers, corners: answers.corners + 1, sides: [...answers.sides, ''] }
}

/** True when every question has an answer and every side field a valid length. */
export function isComplete(chips: ChipsEvent, answers: ClarifyAnswers): boolean {
  return chips.questions.every((question) => {
    if (question.kind === 'layout') {
      return (
        answers.cornerOption !== null &&
        answers.sides.every((side) => validSide(side) !== null) &&
        !hasSideTooLong(chips, answers)
      )
    }
    return question.id in answers.single
  })
}

/**
 * The answers for the next request (see RequestAnswers): the picked option
 * values, the sides as numbers and the closed flag. A straight fence whose
 * length came from the message sends that length as its one side.
 */
export function requestAnswers(chips: ChipsEvent, answers: ClarifyAnswers): RequestAnswers {
  const choices: Record<string, string> = {}
  for (const question of chips.questions) {
    if (question.kind === 'single' && question.id in answers.single) choices[question.id] = answers.single[question.id]
  }
  if (!layoutQuestion(chips)) return { choices, sides: [], closed: false }
  const straight = straightLength(chips, answers)
  if (straight !== null) return { choices, sides: [straight], closed: false }
  const sides = answers.sides.map((side) => validSide(side)).filter((side) => side !== null)
  return { choices, sides, closed: answers.closed && sides.length >= 3 }
}

function sidesText(sides: readonly string[]): string {
  return sides.map((side) => side.trim()).join(' + ')
}

/** "2 kuta", "4 kuta, zatvoreno", "Kutovi: ne znam"; null before the corners are picked. */
export function cornersText(answers: ClarifyAnswers): string | null {
  const { clarify } = copy.chat
  if (answers.cornerOption === null) return null
  if (answers.corners === null) return clarify.cornersUnknown
  const text = clarify.corners(answers.corners)
  return answers.closed ? `${text}${clarify.closedSuffix}` : text
}

/** The "Kutovi" value in the "Vaš projekt" panel: "2", "4 · zatvoreno", "Ne znam"; null before a pick. */
export function panelCornersText(answers: ClarifyAnswers): string | null {
  const { clarify, project } = copy.chat
  if (answers.cornerOption === null) return null
  if (answers.corners === null) return clarify.cornerUnknown
  return answers.closed ? `${answers.corners}${project.closedSuffix}` : String(answers.corners)
}

/**
 * The single questions in panel row order: those in `project.panel_order`
 * first, in that order, then the rest in question order. The canvas lists
 * Boja before Stupovi though the form asks posts first.
 */
export function panelQuestions(chips: ChipsEvent): ChipQuestion[] {
  const singles = chips.questions.filter((question) => question.kind === 'single')
  const order = chips.project?.panel_order ?? []
  const rank = (question: ChipQuestion) => {
    const index = order.indexOf(question.id)
    return index === -1 ? order.length : index
  }
  // Array.prototype.sort is stable, so unlisted questions keep their order.
  return [...singles].sort((a, b) => rank(a) - rank(b))
}

/** The customer's bubble: the corners, the sides and the picked labels, in question order. */
export function answerSummary(chips: ChipsEvent, answers: ClarifyAnswers): string {
  return chips.questions
    .flatMap((question) => {
      if (question.kind === 'layout') {
        return [cornersText(answers), answers.sides.length > 0 ? `${sidesText(answers.sides)} m` : null]
      }
      return [question.options.find((option) => option.value === answers.single[question.id])?.label ?? null]
    })
    .filter((part) => part !== null)
    .join(' · ')
}
