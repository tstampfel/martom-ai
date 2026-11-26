// The start screen's placeholder types an example letter by letter, pauses,
// erases it at backspace speed and types the next one. This file holds the
// pure step logic so it can be tested without timers; useTypingPlaceholder
// drives it with setTimeout.

export type TypingState = {
  /** Which example is showing. */
  index: number
  /** How many of its letters are visible. */
  length: number
  /** True while the example is being erased. */
  erasing: boolean
}

/** Delays in milliseconds, taken from docs/mockups/{web,mobile}/1-start.html. */
export const TYPING_TIMING = {
  type: 75,
  pause: 1600,
  erase: 22,
  next: 350,
} as const

export const INITIAL_TYPING_STATE: TypingState = { index: 0, length: 0, erasing: false }

/**
 * Returns the next state and how long to wait (ms) before showing it. Moving
 * from "fully typed" to "erasing" changes nothing visible, so its delay is the
 * pause on the full example.
 */
export function nextTypingStep(state: TypingState, examples: readonly string[]): { state: TypingState; delay: number } {
  const full = examples[state.index] ?? ''

  if (!state.erasing && state.length < full.length) {
    return { state: { ...state, length: state.length + 1 }, delay: TYPING_TIMING.type }
  }
  if (!state.erasing) {
    return { state: { ...state, erasing: true }, delay: TYPING_TIMING.pause }
  }
  if (state.length > 0) {
    return { state: { ...state, length: state.length - 1 }, delay: TYPING_TIMING.erase }
  }
  return {
    state: { index: (state.index + 1) % examples.length, length: 0, erasing: false },
    delay: TYPING_TIMING.next,
  }
}

/** The visible part of the current example. */
export function typingText(state: TypingState, examples: readonly string[]): string {
  return (examples[state.index] ?? '').slice(0, state.length)
}
