import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { BomRecalcRequest, BomRecalculator } from './api/bomRecalculator'
import type { BomEvent, ChatEvent } from './api/events'
import { recalculateBom } from './api/fakeBomRecalculator'
import { fixtures } from './api/fixtures'
import { useBomEditor } from './useBomEditor'

function bomOf(events: ChatEvent[]): BomEvent {
  const bom = events.find((event): event is BomEvent => event.type === 'bom')
  if (!bom) throw new Error('fixture has no bom event')
  return bom
}

/** A recalculator whose answers the test releases by hand, in any order. */
function manualRecalculator() {
  const calls: { request: BomRecalcRequest; resolve: (bom: BomEvent) => void; reject: (error: Error) => void }[] = []
  const recalculator: BomRecalculator = {
    recalculate(request) {
      return new Promise((resolve, reject) => calls.push({ request, resolve, reject }))
    },
  }
  return { recalculator, calls }
}

const FENCE = bomOf(fixtures.fenceBom)

describe('useBomEditor', () => {
  it('is null while there is no bom event', () => {
    const { recalculator } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(null, recalculator))
    expect(result.current).toBeNull()
  })

  it('starts from the event quantities, not pending', () => {
    const { recalculator } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(FENCE, recalculator))
    expect(result.current!.bom).toBe(FENCE)
    expect(result.current!.quantities.panel).toBe(10)
    expect(result.current!.pending).toBe(false)
  })

  it('sends every quantity to the recalculator and shows the bom event it returns', async () => {
    const { recalculator, calls } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(FENCE, recalculator))
    act(() => result.current!.setQuantity('panel', 11))
    expect(result.current!.quantities.panel).toBe(11)
    expect(result.current!.pending).toBe(true)
    expect(result.current!.bom.total).toBe('913.10')
    expect(calls).toHaveLength(1)
    expect(calls[0].request.quantities).toEqual({ panel: 11, post_base_plate: 11, pvc_clip: 52, anchor_m8x80: 44 })

    const answer = { ...recalculateBom(FENCE, calls[0].request.quantities), total: '999.99' }
    await act(async () => calls[0].resolve(answer))
    expect(result.current!.bom).toBe(answer)
    expect(result.current!.pending).toBe(false)
  })

  it('never goes below 0', () => {
    const { recalculator, calls } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(FENCE, recalculator))
    act(() => result.current!.setQuantity('panel', -1))
    expect(result.current!.quantities.panel).toBe(0)
    expect(calls[0].request.quantities.panel).toBe(0)
  })

  it('ignores an older answer that arrives after a newer one', async () => {
    const { recalculator, calls } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(FENCE, recalculator))
    act(() => result.current!.setQuantity('panel', 11))
    act(() => result.current!.setQuantity('panel', 12))
    const newer = recalculateBom(FENCE, calls[1].request.quantities)
    await act(async () => calls[1].resolve(newer))
    await act(async () => calls[0].resolve(recalculateBom(FENCE, calls[0].request.quantities)))
    expect(result.current!.bom).toBe(newer)
    expect(result.current!.quantities.panel).toBe(12)
    expect(result.current!.pending).toBe(false)
  })

  it('goes back to the last event quantities and reports the failure when a recalculation fails', async () => {
    const { recalculator, calls } = manualRecalculator()
    const { result } = renderHook(() => useBomEditor(FENCE, recalculator))
    act(() => result.current!.setQuantity('panel', 11))
    await act(async () => calls[0].reject(new Error('network')))
    expect(result.current!.failed).toBe(true)
    expect(result.current!.pending).toBe(false)
    expect(result.current!.quantities.panel).toBe(10)
    expect(result.current!.bom).toBe(FENCE)

    act(() => result.current!.setQuantity('panel', 11))
    expect(result.current!.failed).toBe(false)
  })

  it('starts afresh when a new bom event arrives', async () => {
    const { recalculator } = manualRecalculator()
    const { result, rerender } = renderHook(({ bom }) => useBomEditor(bom, recalculator), {
      initialProps: { bom: FENCE },
    })
    act(() => result.current!.setQuantity('panel', 11))
    const other = bomOf(fixtures.rollFenceSomeOnRequest)
    rerender({ bom: other })
    await waitFor(() => expect(result.current!.bom).toBe(other))
    expect(result.current!.pending).toBe(false)
    expect(result.current!.quantities).toEqual({
      roll_welded: 1,
      post_foot_plate: 14,
      brace_t_post: 2,
      tensioner: 3,
      anchor_m8x70: 42,
    })
  })
})
