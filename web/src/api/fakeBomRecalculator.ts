// A BomRecalculator that answers without a server, so the steppers can be
// built and tried before the real recalculation API (#26) exists.
//
// It plays the backend's part: it takes the unit prices from the bom event it
// was given (which came from the fixtures) and returns a new bom event. This
// arithmetic stands in for the server; no component does it. The real BOM
// math is Python (src/martom/bom/), and #26 replaces this file.

import type { BomRecalculator } from './bomRecalculator'
import type { BomEvent, BomLine, Price } from './events'

/** "53.00" → 5300. Prices from the API always have two decimals. */
function toCents(price: Price): number {
  const [whole, fraction = '00'] = price.split('.')
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0').slice(0, 2))
}

/** 5300 → "53.00". */
function fromCents(cents: number): Price {
  const whole = Math.floor(cents / 100)
  const fraction = String(cents % 100).padStart(2, '0')
  return `${whole}.${fraction}`
}

function recalculatedLine(line: BomLine, quantity: number): BomLine {
  if (line.unit_price === null) return { ...line, quantity }
  return { ...line, quantity, line_total: fromCents(toCents(line.unit_price) * quantity) }
}

/** The same bom with the given quantities, line totals and total. */
export function recalculateBom(bom: BomEvent, quantities: Record<string, number>): BomEvent {
  const lines = bom.lines.map((line) => recalculatedLine(line, quantities[line.item] ?? line.quantity))
  const priced = lines.filter((line) => line.line_total !== null)
  const totalCents = priced.reduce((sum, line) => sum + toCents(line.line_total as Price), 0)
  return {
    ...bom,
    lines,
    total: priced.length === 0 ? null : fromCents(totalCents),
    unpriced_count: lines.length - priced.length,
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

type FakeOptions = {
  /** How to wait before answering; tests pass one that resolves at once. */
  wait?: (ms: number) => Promise<void>
}

export function createFakeBomRecalculator({ wait = sleep }: FakeOptions = {}): BomRecalculator {
  return {
    async recalculate({ bom, quantities }, signal) {
      // A short pause, like a round trip, so the "updating" state is visible.
      await wait(150)
      signal?.throwIfAborted()
      return recalculateBom(bom, quantities)
    },
  }
}
