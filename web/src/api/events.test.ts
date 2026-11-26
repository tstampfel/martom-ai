// Contract test: every recorded event stream matches the JSON Schema that the
// backend generates from its Pydantic models (docs/api/chat-events.schema.json).
// The fixtures are typed as ChatEvent[], so `npm run typecheck` checks them
// against events.ts; this test checks them against the schema. A change on
// either side without the other breaks the frontend checks.

import Ajv2020 from 'ajv/dist/2020'
import { describe, expect, it } from 'vitest'
import { BOM_ITEMS, CHAT_EVENT_TYPES, type BomEvent, type ChatEvent } from './events'
import { fixtures } from './fixtures'
import schema from '../../../docs/api/chat-events.schema.json'

// strict: false because the schema carries Pydantic's `discriminator` keyword,
// which is OpenAPI vocabulary, not JSON Schema; Ajv would refuse it otherwise.
const validate = new Ajv2020({ strict: false, allErrors: true }).compile(schema)

function cents(price: string): number {
  return Math.round(Number(price) * 100)
}

describe('chat event contract', () => {
  it('lists the same event types as the schema', () => {
    expect([...CHAT_EVENT_TYPES].sort()).toEqual(Object.keys(schema.discriminator.mapping).sort())
  })

  it('lists the same BOM items as the schema', () => {
    expect([...BOM_ITEMS].sort()).toEqual([...schema.$defs.Item.enum].sort())
  })

  for (const [name, events] of Object.entries(fixtures)) {
    describe(`fixture ${name}`, () => {
      it.each(events.map((event, index) => [index, event.type, event] as const))(
        'event %i (%s) matches the JSON Schema',
        (_index, _type, event) => {
          const valid = validate(event)
          expect(validate.errors ?? []).toEqual([])
          expect(valid).toBe(true)
        },
      )

      it('ends with exactly one done event', () => {
        expect(events.at(-1)?.type).toBe('done')
        expect(events.filter((event) => event.type === 'done')).toHaveLength(1)
      })

      it('has BOM prices that add up', () => {
        // The backend models check these rules too; JSON Schema can't express them.
        const boms = events.filter((event): event is BomEvent => event.type === 'bom')
        for (const bom of boms) {
          const lines = bom.lines.map((line) => ({
            onRequest: line.price_on_request,
            hasPrices: line.unit_price !== null && line.line_total !== null,
            totalOk:
              line.unit_price === null ||
              line.line_total === null ||
              cents(line.line_total) === cents(line.unit_price) * line.quantity,
          }))
          expect(lines).toEqual(lines.map((line) => ({ onRequest: !line.hasPrices, hasPrices: line.hasPrices, totalOk: true })))
          const sum = bom.lines.reduce((acc, line) => acc + (line.line_total === null ? 0 : cents(line.line_total)), 0)
          expect(cents(bom.total)).toBe(sum)
        }
      })
    })
  }

  it('covers every event type across the fixtures', () => {
    const seen = new Set(Object.values(fixtures).flatMap((events) => events.map((event) => event.type)))
    expect([...seen].sort()).toEqual([...CHAT_EVENT_TYPES].sort())
  })

  it('has a rate-limited reply', () => {
    const errors = Object.values(fixtures)
      .flat()
      .filter((event) => event.type === 'error')
    expect(errors.some((event) => event.kind === 'rate_limited' && event.retry_after_seconds !== null)).toBe(true)
  })

  it('rejects an event that breaks the schema', () => {
    const broken: unknown[] = [
      { type: 'token', text: 'x', extra: 1 },
      { type: 'debug', text: 'x' },
      { type: 'error', kind: 'rate_limited' },
    ]
    for (const event of broken) {
      expect(validate(event)).toBe(false)
    }
    const floatPrice = { ...fixtures.fenceBom.find((e) => e.type === 'bom'), total: 530 }
    expect(validate(floatPrice as unknown as ChatEvent)).toBe(false)
  })
})
