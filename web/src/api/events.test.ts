// Contract test: events.ts, the recorded fixture streams and the JSON Schema
// that the backend generates from its Pydantic models
// (docs/api/chat-events.schema.json) must describe the same events.
//
// - Every fixture event is validated against the schema (Ajv). The fixtures
//   are typed as ChatEvent[], so `npm run typecheck` checks them against
//   events.ts.
// - SHAPES lists every field of every schema object and whether it can be
//   null. Typecheck holds SHAPES to events.ts; this test holds it to the
//   schema. So a field added, removed or made nullable on one side only fails.
// - ENUMS holds every closed set of values; a new error kind, step status or
//   chip kind in the schema fails until events.ts has it too.

import Ajv2020 from 'ajv/dist/2020'
import { describe, expect, it } from 'vitest'
import schema from '../../../docs/api/chat-events.schema.json'
import {
  CHAT_EVENT_TYPES,
  CHIP_KINDS,
  ERROR_KINDS,
  STEP_STATUSES,
  type BomEvent,
  type BomLine,
  type ChipOption,
  type ChipQuestion,
  type ChipsEvent,
  type DoneEvent,
  type ErrorEvent,
  type ProductCard,
  type ProductCardsEvent,
  type StepEvent,
  type TokenEvent,
  type Variant,
} from './events'
import { fixtures } from './fixtures'

// strict: false because the schema carries Pydantic's `discriminator` keyword,
// which is OpenAPI vocabulary, not JSON Schema; Ajv would refuse it otherwise.
const validate = new Ajv2020({ strict: false, allErrors: true }).compile(schema)

/** For each field of T: true when it can be null. Every field must be listed. */
type Nullability<T> = { [K in keyof T]-?: null extends T[K] ? true : false }

// Keys are the schema's $defs names (BomLineOut is BomLine in events.ts).
const SHAPES = {
  TokenEvent: { type: false, text: false } satisfies Nullability<TokenEvent>,
  StepEvent: { type: false, id: false, text: false, status: false } satisfies Nullability<StepEvent>,
  ChipOption: { value: false, label: false, badge: true, note: true } satisfies Nullability<ChipOption>,
  ChipQuestion: { id: false, text: false, kind: false, options: false } satisfies Nullability<ChipQuestion>,
  ChipsEvent: { type: false, intro: true, questions: false } satisfies Nullability<ChipsEvent>,
  Variant: { label: false, price: true } satisfies Nullability<Variant>,
  ProductCard: {
    listing_id: false,
    url: false,
    title: false,
    subtitle: true,
    why: false,
    badge: true,
    photo_url: true,
    price: true,
    price_unit: true,
    variants: false,
  } satisfies Nullability<ProductCard>,
  ProductCardsEvent: { type: false, cards: false } satisfies Nullability<ProductCardsEvent>,
  BomLineOut: {
    item: false,
    name: false,
    size: true,
    quantity: false,
    unit_price: true,
    line_total: true,
    price_on_request: false,
    made_to_order: false,
    label: true,
  } satisfies Nullability<BomLine>,
  BomEvent: {
    type: false,
    title: false,
    summary: false,
    lines: false,
    total: true,
    unpriced_count: false,
    notes: false,
  } satisfies Nullability<BomEvent>,
  ErrorEvent: { type: false, kind: false, retry_after_seconds: true } satisfies Nullability<ErrorEvent>,
  DoneEvent: { type: false, thread_id: false } satisfies Nullability<DoneEvent>,
}

const ENUMS: Record<string, readonly string[]> = {
  'StepEvent.status': STEP_STATUSES,
  'ChipQuestion.kind': CHIP_KINDS,
  'ErrorEvent.kind': ERROR_KINDS,
}

interface SchemaProperty {
  enum?: string[]
  anyOf?: SchemaProperty[]
  type?: string
}

const defs = schema.$defs as Record<string, { properties: Record<string, SchemaProperty> }>

function cents(price: string): number {
  return Math.round(Number(price) * 100)
}

function allBoms(): BomEvent[] {
  return Object.values(fixtures)
    .flat()
    .filter((event): event is BomEvent => event.type === 'bom')
}

describe('chat event contract', () => {
  it('lists the same event types as the schema', () => {
    expect([...CHAT_EVENT_TYPES].sort()).toEqual(Object.keys(schema.discriminator.mapping).sort())
  })

  it('has the same objects, fields and nullability as the schema', () => {
    const fromSchema = Object.fromEntries(
      Object.entries(defs).map(([name, def]) => [
        name,
        Object.fromEntries(
          Object.entries(def.properties).map(([field, prop]) => [
            field,
            (prop.anyOf ?? []).some((option) => option.type === 'null'),
          ]),
        ),
      ]),
    )
    expect(fromSchema).toEqual(SHAPES)
  })

  it('has the same closed sets of values as the schema', () => {
    const fromSchema: Record<string, string[]> = {}
    for (const [name, def] of Object.entries(defs)) {
      for (const [field, prop] of Object.entries(def.properties)) {
        const values = prop.enum ?? prop.anyOf?.find((option) => option.enum)?.enum
        if (values) fromSchema[`${name}.${field}`] = [...values].sort()
      }
    }
    const fromTs = Object.fromEntries(Object.entries(ENUMS).map(([key, values]) => [key, [...values].sort()]))
    expect(fromSchema).toEqual(fromTs)
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
    })
  }

  // The backend models check these rules too; JSON Schema can't express them.
  it.each(allBoms().map((bom) => [bom.summary, bom] as const))('BOM %s has prices that add up', (_summary, bom) => {
    const lines = bom.lines.map((line) => ({
      onRequest: line.price_on_request,
      noUnitPrice: line.unit_price === null,
      noLineTotal: line.line_total === null,
      totalIsUnitTimesQuantity:
        line.unit_price === null ||
        line.line_total === null ||
        cents(line.line_total) === cents(line.unit_price) * line.quantity,
    }))
    // price_on_request <=> both prices null; otherwise both are set and agree.
    expect(lines).toEqual(
      bom.lines.map((line) => ({
        onRequest: line.price_on_request,
        noUnitPrice: line.price_on_request,
        noLineTotal: line.price_on_request,
        totalIsUnitTimesQuantity: true,
      })),
    )
    const priced = bom.lines.filter((line) => line.line_total !== null)
    const sum = priced.reduce((acc, line) => acc + cents(line.line_total ?? '0'), 0)
    expect(bom.total === null ? null : cents(bom.total)).toBe(priced.length ? sum : null)
    expect(bom.unpriced_count).toBe(bom.lines.filter((line) => line.price_on_request).length)
  })

  it('covers every event type across the fixtures', () => {
    const seen = new Set(Object.values(fixtures).flatMap((events) => events.map((event) => event.type)))
    expect([...seen].sort()).toEqual([...CHAT_EVENT_TYPES].sort())
  })

  it('covers the three BOM total cases', () => {
    const cases = new Set(
      allBoms().map((bom) => {
        if (bom.total === null) return 'all na upit'
        return bom.unpriced_count > 0 ? 'some na upit' : 'all priced'
      }),
    )
    expect([...cases].sort()).toEqual(['all na upit', 'all priced', 'some na upit'])
  })

  it('has a rate-limited reply', () => {
    const errors = Object.values(fixtures)
      .flat()
      .filter((event) => event.type === 'error')
    expect(errors.some((event) => event.kind === 'rate_limited' && event.retry_after_seconds !== null)).toBe(true)
  })

  it('rejects an event that breaks the schema', () => {
    const [bom] = allBoms()
    const broken: unknown[] = [
      { type: 'token', text: 'x', extra: 1 },
      { type: 'debug', text: 'x' },
      { type: 'error', kind: 'rate_limited' },
      { type: 'error', kind: 'overloaded', retry_after_seconds: null },
      { ...bom, total: 530 },
      { ...bom, unpriced_count: -1 },
    ]
    for (const event of broken) {
      expect(validate(event)).toBe(false)
    }
  })
})
