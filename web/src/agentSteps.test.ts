import { describe, expect, it } from 'vitest'
import { agentStepStates, nextAgentStep } from './agentSteps'
import { TYPING_TIMING } from './typing'

const steps = [{ text: 'ab' }, { text: 'cdef' }, { text: 'xyz' }]

describe('nextAgentStep', () => {
  it('shows the first step after the same short gap the placeholder waits between examples', () => {
    expect(nextAgentStep(0, steps)).toEqual({ shown: 1, delay: TYPING_TIMING.next })
  })

  it('shows each next step after the time it would take to type the one before it', () => {
    expect(nextAgentStep(1, steps)).toEqual({ shown: 2, delay: TYPING_TIMING.type * 'ab'.length })
    expect(nextAgentStep(2, steps)).toEqual({ shown: 3, delay: TYPING_TIMING.type * 'cdef'.length })
  })

  it('clears the card after the last step has worked and the placeholder pause has passed', () => {
    expect(nextAgentStep(3, steps)).toEqual({
      shown: 0,
      delay: TYPING_TIMING.type * 'xyz'.length + TYPING_TIMING.pause,
    })
  })
})

describe('agentStepStates', () => {
  it('hides every step on an empty card', () => {
    expect(agentStepStates(0, 3)).toEqual(['hidden', 'hidden', 'hidden'])
  })

  it('marks the newest step as working and the earlier ones as done', () => {
    expect(agentStepStates(1, 3)).toEqual(['working', 'hidden', 'hidden'])
    expect(agentStepStates(2, 3)).toEqual(['done', 'working', 'hidden'])
  })

  it('keeps the last step working when the card is full', () => {
    expect(agentStepStates(3, 3)).toEqual(['done', 'done', 'working'])
  })
})
