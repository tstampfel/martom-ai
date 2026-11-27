import { describe, expect, it } from 'vitest'
import { fixtures } from './api/fixtures'
import type { ChipsEvent } from './api/events'
import {
  addSide,
  answersForRequest,
  answerSummary,
  canAddSide,
  formatMetres,
  formatPrice,
  initialAnswers,
  isComplete,
  parseMetres,
  pickCorners,
  pickOption,
  resizeSides,
  sideCount,
  sidesTotal,
  toggleClosed,
  type ClarifyAnswers,
} from './clarify'

const chips = fixtures.fenceClarify.find((event): event is ChipsEvent => event.type === 'chips')!

/** A chips event with only the side lengths (no corners question). */
const sidesOnly: ChipsEvent = {
  type: 'chips',
  intro: null,
  questions: [{ id: 'sides', text: 'Dužina svake strane (m)', kind: 'side_lengths', options: [] }],
}

function answered(): ClarifyAnswers {
  let answers = pickCorners(chips, initialAnswers(chips), '2')
  answers = { ...answers, sides: ['10', '5', '10'] }
  answers = pickOption(answers, 'posts', 'plate')
  return pickOption(answers, 'color', 'antracit')
}

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

describe('sideCount', () => {
  it('has one more side than corners for an open fence', () => {
    expect(sideCount(1, false)).toBe(2)
    expect(sideCount(2, false)).toBe(3)
  })

  it('has as many sides as corners when the fence goes all the way round', () => {
    expect(sideCount(4, true)).toBe(4)
  })

  it('asks for no sides with 0 or unknown corners (the length comes from the message)', () => {
    expect(sideCount(0, false)).toBe(0)
    expect(sideCount(null, false)).toBe(0)
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

describe('initialAnswers', () => {
  it('shows no side fields before the corners are picked', () => {
    expect(initialAnswers(chips).sides).toEqual([])
  })

  it('shows one side field when there is no corners question', () => {
    expect(initialAnswers(sidesOnly).sides).toEqual([''])
  })
})

describe('pickCorners', () => {
  it('shows one field per side', () => {
    const answers = pickCorners(chips, initialAnswers(chips), '2')
    expect(answers).toMatchObject({ corners: 2, sides: ['', '', ''], single: { corners: '2' } })
  })

  it('hides the side fields for "Ne znam" and for 0', () => {
    const two = pickCorners(chips, initialAnswers(chips), '2')
    expect(pickCorners(chips, two, 'unknown')).toMatchObject({ corners: null, sides: [] })
    expect(pickCorners(chips, two, '0')).toMatchObject({ corners: 0, sides: [] })
  })

  it('starts "4+" at 4 corners', () => {
    expect(pickCorners(chips, initialAnswers(chips), '4+')).toMatchObject({ corners: 4, sides: ['', '', '', '', ''] })
  })

  it('opens a closed fence again below 3 corners', () => {
    const closed = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), '4+'))
    expect(pickCorners(chips, closed, '3')).toMatchObject({ closed: true, sides: ['', '', ''] })
    expect(pickCorners(chips, closed, '2')).toMatchObject({ closed: false, sides: ['', '', ''] })
  })
})

describe('toggleClosed', () => {
  it('has as many sides as corners when closed', () => {
    const answers = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), '3'))
    expect(answers).toMatchObject({ closed: true, corners: 3, sides: ['', '', ''] })
  })

  it('needs at least 3 corners, so it picks "4+" (4) when fewer or unknown', () => {
    for (const value of ['unknown', '1']) {
      const answers = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), value))
      expect(answers).toMatchObject({ closed: true, corners: 4, single: { corners: '4+' } })
      expect(answers.sides).toHaveLength(4)
    }
  })

  it('opens the fence again on a second press', () => {
    const closed = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), '3'))
    expect(toggleClosed(chips, closed)).toMatchObject({ closed: false, corners: 3, sides: ['', '', '', ''] })
  })
})

describe('addSide', () => {
  it('is offered only from 4 corners, or when there is no corners question', () => {
    expect(canAddSide(chips, pickCorners(chips, initialAnswers(chips), '2'))).toBe(false)
    expect(canAddSide(chips, pickCorners(chips, initialAnswers(chips), '4+'))).toBe(true)
    expect(canAddSide(sidesOnly, initialAnswers(sidesOnly))).toBe(true)
  })

  it('raises the corners with the new side, so the request stays consistent', () => {
    const answers = addSide(chips, pickCorners(chips, initialAnswers(chips), '4+'))
    expect(answers).toMatchObject({ corners: 5, single: { corners: '4+' } })
    expect(answers.sides).toHaveLength(6)
    expect(answersForRequest(chips, { ...answers, sides: ['1', '1', '1', '1', '1', '1'] })).toMatchObject({
      corners: '5',
      sides: '1 + 1 + 1 + 1 + 1 + 1',
    })
  })

  it('only adds a field when there is no corners question', () => {
    expect(addSide(sidesOnly, initialAnswers(sidesOnly))).toMatchObject({ corners: null, sides: ['', ''] })
  })
})

describe('isComplete', () => {
  it('needs every chip question answered and every side filled', () => {
    expect(isComplete(chips, answered())).toBe(true)
    expect(isComplete(chips, pickCorners(chips, initialAnswers(chips), '2'))).toBe(false)
    expect(isComplete(chips, { ...answered(), sides: ['10', '', '10'] })).toBe(false)
  })

  it('completes with "Ne znam" corners and no side lengths', () => {
    const answers = pickCorners(chips, answered(), 'unknown')
    expect(isComplete(chips, answers)).toBe(true)
  })

  it('does not need the closed toggle to be touched', () => {
    expect(answered().closed).toBe(false)
    expect(isComplete(chips, answered())).toBe(true)
  })
})

describe('answersForRequest', () => {
  it('sends option values, the corners count, the closed flag and the side lengths joined', () => {
    expect(answersForRequest(chips, answered())).toEqual({
      corners: '2',
      closed: 'no',
      sides: '10 + 5 + 10',
      posts: 'plate',
      color: 'antracit',
    })
  })

  it('sends "unknown" corners and no sides for "Ne znam"', () => {
    const request = answersForRequest(chips, pickCorners(chips, answered(), 'unknown'))
    expect(request).toMatchObject({ corners: 'unknown', closed: 'no' })
    expect(request).not.toHaveProperty('sides')
  })

  it('sends closed "yes" when the fence goes round', () => {
    const answers = toggleClosed(chips, pickCorners(chips, answered(), '4+'))
    expect(answersForRequest(chips, answers)).toMatchObject({ corners: '4', closed: 'yes' })
  })
})

describe('answerSummary', () => {
  it('names the corners and lists the sides and the picked labels for the customer bubble', () => {
    expect(answerSummary(chips, answered())).toBe('2 kuta · 10 + 5 + 10 m · Na beton, s pločicom · Antracit (RAL 7016)')
  })

  it('says when the fence goes round and leaves out missing sides', () => {
    const closed = toggleClosed(chips, pickCorners(chips, answered(), '4+'))
    expect(answerSummary(chips, { ...closed, sides: ['5', '5', '5', '5'] })).toBe(
      '4 kuta, zatvoreno · 5 + 5 + 5 + 5 m · Na beton, s pločicom · Antracit (RAL 7016)',
    )
    expect(answerSummary(chips, pickCorners(chips, answered(), 'unknown'))).toBe(
      'Kutovi: ne znam · Na beton, s pločicom · Antracit (RAL 7016)',
    )
  })
})
