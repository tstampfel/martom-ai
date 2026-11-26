import { describe, expect, it } from 'vitest'
import { TYPING_TIMING, nextTypingStep, typingText, type TypingState } from './typing'

const examples = ['ab', 'xyz']
const start: TypingState = { index: 0, length: 0, erasing: false }

/** Runs `count` steps from `state` and returns every visible text on the way. */
function run(state: TypingState, count: number) {
  const texts: string[] = []
  const delays: number[] = []
  let current = state
  for (let i = 0; i < count; i++) {
    const step = nextTypingStep(current, examples)
    current = step.state
    delays.push(step.delay)
    texts.push(typingText(current, examples))
  }
  return { texts, delays, state: current }
}

describe('nextTypingStep', () => {
  it('types the example one letter at a time', () => {
    expect(run(start, 2).texts).toEqual(['a', 'ab'])
  })

  it('pauses on the full example before erasing', () => {
    const { delays, state } = run(start, 3)
    expect(state).toEqual({ index: 0, length: 2, erasing: true })
    expect(delays[2]).toBe(TYPING_TIMING.pause)
  })

  it('erases letter by letter, faster than it types', () => {
    const { texts, delays } = run(start, 5)
    expect(texts.slice(3)).toEqual(['a', ''])
    expect(delays[3]).toBe(TYPING_TIMING.erase)
    expect(TYPING_TIMING.erase).toBeLessThan(TYPING_TIMING.type)
  })

  it('moves to the next example after erasing', () => {
    const { texts, state } = run(start, 7)
    expect(state).toEqual({ index: 1, length: 1, erasing: false })
    expect(texts.slice(5)).toEqual(['', 'x'])
  })

  it('wraps from the last example back to the first', () => {
    const lastEmpty: TypingState = { index: 1, length: 0, erasing: true }
    expect(nextTypingStep(lastEmpty, examples).state).toEqual({ index: 0, length: 0, erasing: false })
  })
})
