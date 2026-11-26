import { describe, expect, it } from 'vitest'
import { fixtures } from './api/fixtures'
import type { ChipsEvent } from './api/events'
import {
  answersForRequest,
  answerSummary,
  formatMetres,
  formatPrice,
  isComplete,
  parseMetres,
  resizeSides,
  sideCountFor,
  sidesTotal,
  type ClarifyAnswers,
} from './clarify'

const chips = fixtures.fenceClarify.find((event): event is ChipsEvent => event.type === 'chips')!

describe('formatPrice', () => {
  it('shows an API price with a decimal comma and KM', () => {
    expect(formatPrice('53.00')).toBe('53,00 KM')
    expect(formatPrice('1234.50')).toBe('1234,50 KM')
  })
})

describe('parseMetres and formatMetres', () => {
  it('reads a decimal comma or dot and rejects anything else', () => {
    expect(parseMetres('10')).toBe(10)
    expect(parseMetres('7,5')).toBe(7.5)
    expect(parseMetres(' 2.25 ')).toBe(2.25)
    expect(parseMetres('')).toBeNull()
    expect(parseMetres('abc')).toBeNull()
    expect(parseMetres('0')).toBeNull()
    expect(parseMetres('-3')).toBeNull()
  })

  it('writes metres with a decimal comma', () => {
    expect(formatMetres(25)).toBe('25')
    expect(formatMetres(17.5)).toBe('17,5')
  })
})

describe('sideCountFor', () => {
  it('has one more side than corners for an open fence', () => {
    expect(sideCountFor('0')).toBe(1)
    expect(sideCountFor('2')).toBe(3)
    expect(sideCountFor('4+')).toBe(5)
  })

  it('starts with one side when the corners are unknown or unanswered', () => {
    expect(sideCountFor('unknown')).toBe(1)
    expect(sideCountFor(undefined)).toBe(1)
  })
})

describe('resizeSides', () => {
  it('keeps what was typed and pads or cuts to the new count', () => {
    expect(resizeSides(['10', '5'], 3)).toEqual(['10', '5', ''])
    expect(resizeSides(['10', '5', '10'], 1)).toEqual(['10'])
  })
})

describe('sidesTotal', () => {
  it('adds the sides that are valid numbers', () => {
    expect(sidesTotal(['10', '5', '10'])).toBe(25)
    expect(sidesTotal(['10', '', 'x'])).toBe(10)
    expect(sidesTotal([])).toBe(0)
  })
})

const answered: ClarifyAnswers = {
  single: { corners: '2', posts: 'plate', color: 'antracit' },
  sides: ['10', '5', '10'],
}

describe('isComplete', () => {
  it('needs every single question answered and every side filled', () => {
    expect(isComplete(chips, answered)).toBe(true)
    expect(isComplete(chips, { ...answered, single: { corners: '2', posts: 'plate' } })).toBe(false)
    expect(isComplete(chips, { ...answered, sides: ['10', '', '10'] })).toBe(false)
  })
})

describe('answersForRequest', () => {
  it('sends option values and the side lengths joined', () => {
    expect(answersForRequest(chips, answered)).toEqual({
      corners: '2',
      sides: '10 + 5 + 10',
      posts: 'plate',
      color: 'antracit',
    })
  })
})

describe('answerSummary', () => {
  it('joins the picked labels and the sides for the customer bubble', () => {
    expect(answerSummary(chips, answered)).toBe('2 · 10 + 5 + 10 m · Na beton, s pločicom · Antracit (RAL 7016)')
  })
})
