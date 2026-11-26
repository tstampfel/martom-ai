import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { nextAgentStep } from './agentSteps'
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

function subscribePageVisibility(onChange: () => void): () => void {
  document.addEventListener('visibilitychange', onChange)
  return () => document.removeEventListener('visibilitychange', onChange)
}

/** False while the browser tab is hidden (another tab, minimised window). */
export function usePageVisible(): boolean {
  return useSyncExternalStore(
    subscribePageVisibility,
    () => document.visibilityState !== 'hidden',
    () => true,
  )
}

/**
 * How many of the agent card's example steps to show. They appear one at a
 * time, pause when all are showing, clear and loop (see agentSteps.ts).
 * With reduced motion every step shows and no timer runs. While the tab is
 * hidden the loop stops; it carries on from where it stopped either way.
 */
export function useAgentCardSteps(steps: readonly { text: string }[]): number {
  const reducedMotion = usePrefersReducedMotion()
  const pageVisible = usePageVisible()
  const paused = reducedMotion || !pageVisible
  const [shown, setShown] = useState(0)
  // Same pattern as useTypingPlaceholder: the timer chain reads the latest
  // count from a ref, so it resumes where it paused.
  const shownRef = useRef(0)

  useEffect(() => {
    if (paused) return
    let timer: ReturnType<typeof setTimeout>
    function scheduleNextStep() {
      const { shown: next, delay } = nextAgentStep(shownRef.current, steps)
      timer = setTimeout(() => {
        shownRef.current = next
        setShown(next)
        scheduleNextStep()
      }, delay)
    }
    scheduleNextStep()
    return () => clearTimeout(timer)
  }, [steps, paused])

  return reducedMotion ? steps.length : shown
}
