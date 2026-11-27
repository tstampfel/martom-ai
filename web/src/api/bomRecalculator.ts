// The one interface the materials list card uses when the customer changes a
// quantity. The browser never computes prices or totals itself (grounding
// rule): it sends the new quantities and shows the bom event that comes back.
// The fake (fakeBomRecalculator.ts) stands in until the real API call lands
// in #26, which implements this same interface.

import type { BomEvent } from './events'

export type BomRecalcRequest = {
  /** The materials list as last shown; the backend knows which BOM it is. */
  bom: BomEvent
  /** BOM item key → the quantity the customer set, for every line. */
  quantities: Record<string, number>
}

export interface BomRecalculator {
  /**
   * Resolves with the recalculated bom event. Rejects when the request fails
   * or `signal` aborts.
   */
  recalculate(request: BomRecalcRequest, signal?: AbortSignal): Promise<BomEvent>
}
