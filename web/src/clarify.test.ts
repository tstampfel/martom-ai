import { describe, expect, it } from 'vitest'
import { fixtures } from './api/fixtures'
import type { ChipsEvent } from './api/events'
import {
  addSide,
  answerSummary,
  canAddSide,
  formatMetres,
  formatPrice,
  hasSideTooLong,
  initialAnswers,
  isComplete,
  panelCornersText,
  parseMetres,
  pickCorners,
  pickOption,
  requestAnswers,
  resizeSides,
  sideCount,
  sidesTotal,
  toggleClosed,
  type ClarifyAnswers,
} from './clarify'

const chips = fixtures.fenceClarify.find((event): event is ChipsEvent => event.type === 'chips')!

/** The same questions, but the advisor didn't understand a length from the message. */
const noLength: ChipsEvent = { ...chips, project: null }

/** A chips event without a layout question. */
const colorOnly: ChipsEvent = { ...chips, project: null, questions: chips.questions.filter((q) => q.id === 'color') }

function answered(): ClarifyAnswers {
  let answers = pickCorners(chips, initialAnswers(chips), '2')
  answers = { ...answers, sides: ['10', '5', '10'] }
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
    expect(sideCount(1, false, true)).toBe(2)
    expect(sideCount(2, false, true)).toBe(3)
  })

  it('has as many sides as corners when the fence goes all the way round', () => {
    expect(sideCount(4, true, true)).toBe(4)
  })

  it('asks for no sides with unknown corners, or 0 corners when the length is known', () => {
    expect(sideCount(0, false, true)).toBe(0)
    expect(sideCount(null, false, true)).toBe(0)
  })

  it('asks for the one side of a straight fence when the length is not known', () => {
    expect(sideCount(0, false, false)).toBe(1)
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

  it('preselects the default option the advisor sent (base-plate posts)', () => {
    expect(initialAnswers(chips).single).toEqual({ posts: 'plate' })
  })
})

describe('pickCorners', () => {
  it('shows one field per side', () => {
    const answers = pickCorners(chips, initialAnswers(chips), '2')
    expect(answers).toMatchObject({ cornerOption: '2', corners: 2, sides: ['', '', ''] })
  })

  it('hides the side fields for "Ne znam" and for 0 with a known length', () => {
    const two = pickCorners(chips, initialAnswers(chips), '2')
    expect(pickCorners(chips, two, 'unknown')).toMatchObject({ corners: null, sides: [] })
    expect(pickCorners(chips, two, '0')).toMatchObject({ corners: 0, sides: [] })
  })

  it('asks for one side at 0 corners when the length is not known', () => {
    expect(pickCorners(noLength, initialAnswers(noLength), '0')).toMatchObject({ corners: 0, sides: [''] })
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
    for (const value of ['unknown', '1'] as const) {
      const answers = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), value))
      expect(answers).toMatchObject({ closed: true, corners: 4, cornerOption: '4+' })
      expect(answers.sides).toHaveLength(4)
    }
  })

  it('opens the fence again on a second press', () => {
    const closed = toggleClosed(chips, pickCorners(chips, initialAnswers(chips), '3'))
    expect(toggleClosed(chips, closed)).toMatchObject({ closed: false, corners: 3, sides: ['', '', '', ''] })
  })
})

describe('addSide', () => {
  it('is offered only from 4 corners', () => {
    expect(canAddSide(pickCorners(chips, initialAnswers(chips), '2'))).toBe(false)
    expect(canAddSide(pickCorners(chips, initialAnswers(chips), '4+'))).toBe(true)
  })

  it('raises the corners with the new side, so sides and corners agree', () => {
    const answers = addSide(pickCorners(chips, initialAnswers(chips), '4+'))
    expect(answers).toMatchObject({ corners: 5, cornerOption: '4+' })
    expect(answers.sides).toHaveLength(6)
  })
})

describe('isComplete', () => {
  it('needs every chip question answered, the corners picked and every side filled', () => {
    expect(isComplete(chips, answered())).toBe(true)
    expect(isComplete(chips, initialAnswers(chips))).toBe(false)
    expect(isComplete(chips, pickCorners(chips, initialAnswers(chips), '2'))).toBe(false)
    expect(isComplete(chips, { ...answered(), sides: ['10', '', '10'] })).toBe(false)
  })

  it('completes with "Ne znam" corners and no side lengths', () => {
    expect(isComplete(chips, pickCorners(chips, answered(), 'unknown'))).toBe(true)
  })

  it('refuses a side over 300 m (the cap rule)', () => {
    const tooLong = { ...answered(), sides: ['10', '300,5', '10'] }
    expect(isComplete(chips, tooLong)).toBe(false)
    expect(hasSideTooLong(tooLong)).toBe(true)
    expect(isComplete(chips, { ...answered(), sides: ['10', '300', '10'] })).toBe(true)
    expect(hasSideTooLong(answered())).toBe(false)
  })

  it('needs no layout answer without a layout question', () => {
    expect(isComplete(colorOnly, pickOption(initialAnswers(colorOnly), 'color', 'zelena'))).toBe(true)
  })
})

describe('requestAnswers', () => {
  it('sends the choices, the sides as numbers and the closed flag', () => {
    expect(requestAnswers(chips, answered())).toEqual({
      choices: { posts: 'plate', color: 'antracit' },
      sides: [10, 5, 10],
      closed: false,
    })
  })

  it('reads decimal commas', () => {
    expect(requestAnswers(chips, { ...answered(), sides: ['7,5', '5', '10'] }).sides).toEqual([7.5, 5, 10])
  })

  it('sends no sides for "Ne znam"', () => {
    expect(requestAnswers(chips, pickCorners(chips, answered(), 'unknown'))).toMatchObject({ sides: [], closed: false })
  })

  it('sends the known length as the one side of a straight fence', () => {
    expect(requestAnswers(chips, pickCorners(chips, answered(), '0')).sides).toEqual([25])
    const typed = { ...pickCorners(noLength, answered(), '0'), sides: ['18'] }
    expect(requestAnswers(noLength, typed).sides).toEqual([18])
  })

  it('sends closed true with as many sides as corners when the fence goes round', () => {
    const closed = toggleClosed(chips, pickCorners(chips, answered(), '4+'))
    expect(requestAnswers(chips, { ...closed, sides: ['10', '5', '10', '5'] })).toMatchObject({
      sides: [10, 5, 10, 5],
      closed: true,
    })
  })

  it('sends no layout without a layout question', () => {
    const answers = pickOption(initialAnswers(colorOnly), 'color', 'zelena')
    expect(requestAnswers(colorOnly, answers)).toEqual({ choices: { color: 'zelena' }, sides: [], closed: false })
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

describe('panelCornersText', () => {
  it('reads like the mockup panel: "2", "4 · zatvoreno", "Ne znam"', () => {
    expect(panelCornersText(initialAnswers(chips))).toBeNull()
    expect(panelCornersText(answered())).toBe('2')
    expect(panelCornersText(toggleClosed(chips, answered()))).toBe('4 · zatvoreno')
    expect(panelCornersText(pickCorners(chips, answered(), 'unknown'))).toBe('Ne znam')
  })
})
