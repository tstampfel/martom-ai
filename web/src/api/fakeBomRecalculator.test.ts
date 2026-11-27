import { describe, expect, it } from 'vitest'
import type { BomEvent, ChatEvent } from './events'
import { createFakeBomRecalculator } from './fakeBomRecalculator'
import { fixtures } from './fixtures'

function bomOf(events: ChatEvent[]): BomEvent {
  const bom = events.find((event): event is BomEvent => event.type === 'bom')
  if (!bom) throw new Error('fixture has no bom event')
  return bom
}

const noWait = () => Promise.resolve()

describe('fake BOM recalculator', () => {
  it('returns a bom event with the new quantity, line total and total', async () => {
    const recalc = createFakeBomRecalculator({ wait: noWait })
    const bom = bomOf(fixtures.fenceBom)
    const next = await recalc.recalculate({ bom, quantities: { ...quantitiesOf(bom), panel: 11 } })
    const panel = next.lines.find((line) => line.item === 'panel')
    expect(panel?.quantity).toBe(11)
    expect(panel?.line_total).toBe('583.00')
    expect(next.total).toBe('966.10')
    expect(next.unpriced_count).toBe(0)
    expect(next.type).toBe('bom')
  })

  it('adds prices in cents, so 0.1 + 0.2 style float errors never show', async () => {
    const recalc = createFakeBomRecalculator({ wait: noWait })
    const bom = bomOf(fixtures.fenceBom)
    const next = await recalc.recalculate({ bom, quantities: { ...quantitiesOf(bom), anchor_m8x80: 3 } })
    expect(next.lines.find((line) => line.item === 'anchor_m8x80')?.line_total).toBe('3.90')
    expect(next.total).toBe('859.80')
  })

  it('keeps "na upit" lines out of the total and keeps total null when nothing is priced', async () => {
    const recalc = createFakeBomRecalculator({ wait: noWait })
    const some = bomOf(fixtures.rollFenceSomeOnRequest)
    const nextSome = await recalc.recalculate({ bom: some, quantities: { ...quantitiesOf(some), brace_t_post: 5 } })
    expect(nextSome.total).toBe('292.55')
    expect(nextSome.unpriced_count).toBe(1)
    expect(nextSome.lines.find((line) => line.item === 'brace_t_post')?.line_total).toBeNull()

    const all = bomOf(fixtures.bomAllOnRequest)
    const nextAll = await recalc.recalculate({ bom: all, quantities: { ...quantitiesOf(all), roll_welded: 3 } })
    expect(nextAll.total).toBeNull()
    expect(nextAll.unpriced_count).toBe(2)
  })

  it('counts a line set to 0 as nothing, and keeps extra lines and notes as they are', async () => {
    const recalc = createFakeBomRecalculator({ wait: noWait })
    const bom = bomOf(fixtures.fenceConcretePosts)
    const next = await recalc.recalculate({ bom, quantities: { ...quantitiesOf(bom), panel: 0 } })
    expect(next.lines.find((line) => line.item === 'panel')?.line_total).toBe('0.00')
    expect(next.total).toBe('383.10')
    expect(next.extra_lines).toEqual(bom.extra_lines)
    expect(next.notes).toEqual(bom.notes)
  })

  it('rejects when the signal aborts', async () => {
    const recalc = createFakeBomRecalculator({ wait: noWait })
    const controller = new AbortController()
    controller.abort()
    const bom = bomOf(fixtures.fenceBom)
    await expect(recalc.recalculate({ bom, quantities: quantitiesOf(bom) }, controller.signal)).rejects.toThrow(/abort/i)
  })
})

function quantitiesOf(bom: BomEvent): Record<string, number> {
  return Object.fromEntries(bom.lines.map((line) => [line.item, line.quantity]))
}
