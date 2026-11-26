import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { copy } from '../copy'
import { TYPING_TIMING } from '../typing'
import { StartScreen } from './StartScreen'

const firstExample = copy.start.examples[0]
const secondExample = copy.start.examples[1]

function stubReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: reduce && query === '(prefers-reduced-motion: reduce)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  )
}

function questionBox() {
  return screen.getByLabelText(copy.start.inputLabel)
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('StartScreen placeholder', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    stubReducedMotion(false)
  })

  it('types the first example letter by letter', () => {
    render(<StartScreen onStartChat={() => {}} />)
    expect(questionBox()).toHaveAttribute('placeholder', '|')

    act(() => vi.advanceTimersByTime(TYPING_TIMING.type * 3))
    expect(questionBox()).toHaveAttribute('placeholder', `${firstExample.slice(0, 3)}|`)
  })

  it('pauses on the full example, erases it and types the next one', () => {
    render(<StartScreen onStartChat={() => {}} />)

    act(() => vi.advanceTimersByTime(TYPING_TIMING.type * firstExample.length))
    expect(questionBox()).toHaveAttribute('placeholder', `${firstExample}|`)

    // Still the full example halfway through the pause.
    act(() => vi.advanceTimersByTime(TYPING_TIMING.pause / 2))
    expect(questionBox()).toHaveAttribute('placeholder', `${firstExample}|`)

    act(() => vi.advanceTimersByTime(TYPING_TIMING.pause / 2 + TYPING_TIMING.erase * 5))
    expect(questionBox()).toHaveAttribute('placeholder', `${firstExample.slice(0, -5)}|`)

    act(() => vi.advanceTimersByTime(TYPING_TIMING.erase * (firstExample.length - 5)))
    expect(questionBox()).toHaveAttribute('placeholder', '|')

    act(() => vi.advanceTimersByTime(TYPING_TIMING.next + TYPING_TIMING.type * 4))
    expect(questionBox()).toHaveAttribute('placeholder', `${secondExample.slice(0, 4)}|`)
  })

  it('cycles back to the first example after the last one', () => {
    render(<StartScreen onStartChat={() => {}} />)
    const oneExample = (text: string) =>
      TYPING_TIMING.type * text.length + TYPING_TIMING.pause + TYPING_TIMING.erase * text.length + TYPING_TIMING.next

    for (const example of copy.start.examples) {
      act(() => vi.advanceTimersByTime(oneExample(example)))
    }
    act(() => vi.advanceTimersByTime(TYPING_TIMING.type * 2))
    expect(questionBox()).toHaveAttribute('placeholder', `${firstExample.slice(0, 2)}|`)
  })
})

describe('StartScreen with prefers-reduced-motion', () => {
  it('shows a static example and never animates', () => {
    vi.useFakeTimers()
    stubReducedMotion(true)
    render(<StartScreen onStartChat={() => {}} />)
    expect(questionBox()).toHaveAttribute('placeholder', firstExample)

    act(() => vi.advanceTimersByTime(10_000))
    expect(questionBox()).toHaveAttribute('placeholder', firstExample)
  })
})

describe('StartScreen starting a chat', () => {
  beforeEach(() => {
    stubReducedMotion(true)
  })

  it.each(copy.start.chips.map((chip) => [chip.label, chip.message]))(
    'chip "%s" starts a chat with "%s"',
    (label, message) => {
      const onStartChat = vi.fn<(text: string) => void>()
      render(<StartScreen onStartChat={onStartChat} />)
      const chips = screen.getByRole('group', { name: copy.start.chipsLabel })
      fireEvent.click(within(chips).getByRole('button', { name: label }))
      expect(onStartChat).toHaveBeenCalledExactlyOnceWith(message)
    },
  )

  it('submits the typed question with the Pitaj button', () => {
    const onStartChat = vi.fn<(text: string) => void>()
    render(<StartScreen onStartChat={onStartChat} />)
    fireEvent.change(questionBox(), { target: { value: '  Ograda za balkon, 6 m  ' } })
    fireEvent.click(screen.getByRole('button', { name: copy.start.submit }))
    expect(onStartChat).toHaveBeenCalledExactlyOnceWith('Ograda za balkon, 6 m')
  })

  it('submits with Enter, while Shift+Enter keeps typing', () => {
    const onStartChat = vi.fn<(text: string) => void>()
    render(<StartScreen onStartChat={onStartChat} />)
    fireEvent.change(questionBox(), { target: { value: 'Brusilica 125' } })

    fireEvent.keyDown(questionBox(), { key: 'Enter', shiftKey: true })
    expect(onStartChat).not.toHaveBeenCalled()

    fireEvent.keyDown(questionBox(), { key: 'Enter' })
    expect(onStartChat).toHaveBeenCalledExactlyOnceWith('Brusilica 125')
  })

  it('does not start a chat with an empty question', () => {
    const onStartChat = vi.fn<(text: string) => void>()
    render(<StartScreen onStartChat={onStartChat} />)
    fireEvent.change(questionBox(), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: copy.start.submit }))
    expect(onStartChat).not.toHaveBeenCalled()
  })
})
