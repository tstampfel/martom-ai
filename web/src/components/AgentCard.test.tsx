import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextAgentStep } from '../agentSteps'
import { copy } from '../copy'
import { StartScreen } from './StartScreen'

const steps = copy.agentCard.steps

/** A matchMedia stub whose reduced-motion answer the test can flip. */
function stubReducedMotion(initial: boolean) {
  let reduce = initial
  const listeners = new Set<() => void>()
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        get matches() {
          return reduce && query === '(prefers-reduced-motion: reduce)'
        },
        media: query,
        addEventListener: (_: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
      }) as unknown as MediaQueryList,
  )
  return (next: boolean) => {
    reduce = next
    act(() => listeners.forEach((listener) => listener()))
  }
}

/** The look of each step in the hero's card: hidden, working or done. */
function stepStates(container: HTMLElement): (string | null)[] {
  const card = container.querySelector('.agent-card')
  if (!card) throw new Error('no agent card')
  return Array.from(card.querySelectorAll('.agent-card__step')).map((step) => step.getAttribute('data-state'))
}

function waitFor(shown: number) {
  act(() => vi.advanceTimersByTime(nextAgentStep(shown, steps).delay))
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('AgentCard animation', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    stubReducedMotion(false)
  })

  it('reserves room for every step while the card starts empty', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    expect(stepStates(container)).toEqual(['hidden', 'hidden', 'hidden'])
  })

  it('shows the steps one at a time, the newest working and the earlier ones done', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)

    waitFor(0)
    expect(stepStates(container)).toEqual(['working', 'hidden', 'hidden'])
    waitFor(1)
    expect(stepStates(container)).toEqual(['done', 'working', 'hidden'])
    waitFor(2)
    expect(stepStates(container)).toEqual(['done', 'done', 'working'])
  })

  it('pauses on the full card, clears it and loops', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)
    waitFor(1)
    waitFor(2)

    // Still full halfway through the pause.
    act(() => vi.advanceTimersByTime(nextAgentStep(3, steps).delay / 2))
    expect(stepStates(container)).toEqual(['done', 'done', 'working'])

    act(() => vi.advanceTimersByTime(nextAgentStep(3, steps).delay / 2))
    expect(stepStates(container)).toEqual(['hidden', 'hidden', 'hidden'])

    waitFor(0)
    expect(stepStates(container)).toEqual(['working', 'hidden', 'hidden'])
  })

  it('marks done steps with ✓ and the working one with …', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)
    waitFor(1)
    const marks = Array.from(container.querySelectorAll('.agent-card')[0].querySelectorAll('.agent-card__mark'))
    expect(marks.slice(0, 2).map((mark) => mark.textContent)).toEqual(['✓', '…'])
  })

  it('keeps the badge "primjer"', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)
    const badges = container.querySelectorAll('.agent-card__badge')
    expect(badges.length).toBeGreaterThan(0)
    badges.forEach((badge) => expect(badge).toHaveTextContent('primjer'))
  })

  it('clears its timers on unmount', () => {
    const { unmount } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops while the tab is hidden and carries on when it is visible again', () => {
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)

    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    act(() => vi.advanceTimersByTime(60_000))
    expect(stepStates(container)).toEqual(['working', 'hidden', 'hidden'])

    visibility.mockReturnValue('visible')
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    waitFor(1)
    expect(stepStates(container)).toEqual(['done', 'working', 'hidden'])
  })
})

describe('AgentCard with prefers-reduced-motion', () => {
  it('shows all three steps at once and schedules no timer', () => {
    vi.useFakeTimers()
    stubReducedMotion(true)
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    expect(stepStates(container)).toEqual(['done', 'done', 'working'])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('follows the setting when it changes while the page is open', () => {
    vi.useFakeTimers()
    const setReduce = stubReducedMotion(false)
    const { container } = render(<StartScreen onStartChat={() => {}} />)
    waitFor(0)
    expect(stepStates(container)).toEqual(['working', 'hidden', 'hidden'])

    setReduce(true)
    expect(stepStates(container)).toEqual(['done', 'done', 'working'])
    expect(vi.getTimerCount()).toBe(0)

    setReduce(false)
    waitFor(1)
    expect(stepStates(container)).toEqual(['done', 'working', 'hidden'])
  })
})

describe('AgentCard for screen readers', () => {
  it('offers the full static list of steps, whatever the animation shows', () => {
    vi.useFakeTimers()
    stubReducedMotion(false)
    render(<StartScreen onStartChat={() => {}} />)
    const [list] = screen.getAllByRole('list', { name: `${copy.agentCard.title}, ${copy.agentCard.badge}` })
    expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual(steps.map((step) => step.text))
  })
})
