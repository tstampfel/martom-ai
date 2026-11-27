import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { nextAgentStep } from './agentSteps'
import { INITIAL_TYPING_STATE, nextTypingStep, typingText } from './typing'

/**
 * True while the CSS media query matches. Follows live changes. jsdom and
 * very old browsers have no matchMedia: there it is always false.
 */
export function useMediaQuery(query: string): boolean {
  // Stable per query, so React subscribes once instead of on every render.
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window.matchMedia !== 'function') return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query],
  )
  return useSyncExternalStore(
    subscribe,
    () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
    () => false,
  )
}

/** True when the visitor asked the OS for less motion (false without matchMedia: motion is fine). */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
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
 * With reduced motion every step shows and no timer runs; when motion is
 * allowed again the loop restarts from an empty card. While the tab is
 * hidden the loop stops and later carries on from where it stopped.
 */
export function useAgentCardSteps(steps: readonly { text: string }[]): number {
  const reducedMotion = usePrefersReducedMotion()
  const pageVisible = usePageVisible()
  const paused = reducedMotion || !pageVisible
  const [shown, setShown] = useState(0)

  // When reduced motion switches on, reset the count during render (React's
  // "adjust state when an input changes" pattern, no effect needed). The
  // full card shows meanwhile, and turning motion back on starts cleanly
  // from an empty card instead of an old step count.
  const [wasReduced, setWasReduced] = useState(reducedMotion)
  if (wasReduced !== reducedMotion) {
    setWasReduced(reducedMotion)
    if (reducedMotion) setShown(0)
  }

  // One timer per step: each change of `shown` schedules the next one.
  useEffect(() => {
    if (paused) return
    const { shown: next, delay } = nextAgentStep(shown, steps)
    const timer = setTimeout(() => setShown(next), delay)
    return () => clearTimeout(timer)
  }, [steps, paused, shown])

  return reducedMotion ? steps.length : shown
}
