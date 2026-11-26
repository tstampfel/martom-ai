// The start screen's "Savjetnik radi" card is a fixed example ("primjer"),
// but its steps appear one at a time so it feels alive: each new step is
// "working" until the next one arrives, then shows ✓. When all of them are
// showing, the card pauses, clears and starts again. This file holds the
// pure step logic so it can be tested without timers; useAgentCardSteps
// drives it with setTimeout. The rhythm reuses the placeholder's timing.

import { TYPING_TIMING } from './typing'

export type AgentStepState = 'hidden' | 'working' | 'done'

/**
 * Returns how many steps to show next and how long to wait (ms) before
 * showing them. A step stays "working" for as long as typing its text would
 * take; the full card then holds for the placeholder's pause before clearing.
 */
export function nextAgentStep(shown: number, steps: readonly { text: string }[]): { shown: number; delay: number } {
  if (shown === 0) {
    return { shown: 1, delay: TYPING_TIMING.next }
  }
  const newest = steps[shown - 1]?.text ?? ''
  const workTime = TYPING_TIMING.type * newest.length
  if (shown < steps.length) {
    return { shown: shown + 1, delay: workTime }
  }
  return { shown: 0, delay: workTime + TYPING_TIMING.pause }
}

/** The look of each of `total` steps when the first `shown` are visible. */
export function agentStepStates(shown: number, total: number): AgentStepState[] {
  return Array.from({ length: total }, (_, index) => {
    if (index >= shown) return 'hidden'
    return index === shown - 1 ? 'working' : 'done'
  })
}
