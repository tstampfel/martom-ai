import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { INITIAL_TYPING_STATE, nextTypingStep, typingText } from './typing'

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

function subscribeReducedMotion(onChange: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => {}
  const query = window.matchMedia(REDUCED_MOTION_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function getReducedMotion(): boolean {
  // jsdom and very old browsers have no matchMedia: assume motion is fine.
  return typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches
}

/** True when the visitor asked the OS for less motion. Follows live changes. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, getReducedMotion, () => false)
}

/**
 * The animated placeholder: types each example, pauses, erases it and types
 * the next, forever. Returns the visible part (`text`) and the whole current
 * example (`example`). While `paused` (reduced motion, focus, or the customer
 * is typing) the timer stops and later resumes where it left off.
 */
export function useTypingPlaceholder(
  examples: readonly string[],
  paused: boolean,
): { text: string; example: string } {
  const [state, setState] = useState(INITIAL_TYPING_STATE)
  // The timer chain reads the latest step from a ref, so it runs without
  // waiting for a re-render between steps and resumes where it paused.
  const stateRef = useRef(INITIAL_TYPING_STATE)

  useEffect(() => {
    if (paused) return
    let timer: ReturnType<typeof setTimeout>
    function scheduleNextStep() {
      const { state: next, delay } = nextTypingStep(stateRef.current, examples)
      timer = setTimeout(() => {
        stateRef.current = next
        setState(next)
        scheduleNextStep()
      }, delay)
    }
    scheduleNextStep()
    return () => clearTimeout(timer)
  }, [examples, paused])

  return { text: typingText(state, examples), example: examples[state.index] ?? '' }
}
