import { useEffect, useRef, useState } from 'react'
import type { BomRecalculator } from './api/bomRecalculator'
import type { BomEvent } from './api/events'

type EditorState = {
  /** The bom event from the chat this state belongs to. */
  source: BomEvent
  /** The latest bom event: the source, or the newest recalculation. */
  bom: BomEvent
  /** What the steppers show; ahead of `bom` while a recalculation runs. */
  quantities: Record<string, number>
  /** Counts the requests; an answer to an older one is ignored. */
  requestId: number
  pending: boolean
  failed: boolean
}

function quantitiesOf(bom: BomEvent): Record<string, number> {
  return Object.fromEntries(bom.lines.map((line) => [line.item, line.quantity]))
}

function initialState(source: BomEvent): EditorState {
  return { source, bom: source, quantities: quantitiesOf(source), requestId: 0, pending: false, failed: false }
}

/**
 * The materials list's quantities. `setQuantity` updates the stepper at once
 * and sends all quantities to the recalculator; the bom event that comes back
 * replaces the shown one, so prices and totals only ever come from an event.
 * When a recalculation fails, the steppers go back to the last event. A new
 * bom event from the chat starts afresh. Returns null while there is no bom.
 */
export function useBomEditor(source: BomEvent | null, recalculator: BomRecalculator) {
  const [state, setState] = useState<EditorState | null>(() => (source ? initialState(source) : null))
  const controllerRef = useRef<AbortController | null>(null)

  // A new bom event from the chat: reset during render (React's "adjust state
  // when a prop changes" pattern). An answer still on its way for the old one
  // is dropped below, because its source no longer matches.
  let current = state
  if ((state?.source ?? null) !== source) {
    current = source ? initialState(source) : null
    setState(current)
  }

  useEffect(() => () => controllerRef.current?.abort(), [])

  if (current === null || source === null) return null
  const editing = current

  function setQuantity(item: string, quantity: number) {
    const quantities = { ...editing.quantities, [item]: Math.max(0, quantity) }
    const requestId = editing.requestId + 1
    const bom = editing.bom
    setState({ ...editing, quantities, requestId, pending: true, failed: false })

    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    // Applies an answer only if it is for the newest request on this source.
    const settle = (update: (latest: EditorState) => EditorState) =>
      setState((latest) => (latest?.source === source && latest.requestId === requestId ? update(latest) : latest))

    recalculator.recalculate({ bom, quantities }, controller.signal).then(
      (next) => settle((latest) => ({ ...latest, bom: next, quantities: quantitiesOf(next), pending: false })),
      () => {
        if (controller.signal.aborted) return
        settle((latest) => ({ ...latest, quantities: quantitiesOf(latest.bom), pending: false, failed: true }))
      },
    )
  }

  return {
    bom: editing.bom,
    quantities: editing.quantities,
    pending: editing.pending,
    failed: editing.failed,
    setQuantity,
  }
}
