// The request body in chatClient.ts and the backend's JSON Schema
// (docs/api/chat-request.schema.json) must describe the same thing:
// - SHAPES lists every field of each object and whether it can be null, and
//   is compared with the schema, so a field changed on one side only fails.
// - The requests the clarify form builds are validated against the schema.

import Ajv2020 from 'ajv/dist/2020'
import { describe, expect, it } from 'vitest'
import schema from '../../../docs/api/chat-request.schema.json'
import { initialAnswers, pickCorners, pickOption, requestAnswers, toggleClosed } from '../clarify'
import { MAX_SIDE_M, type ChatRequest, type RequestAnswers } from './chatClient'
import type { ChipsEvent } from './events'
import { fixtures } from './fixtures'

const validate = new Ajv2020({ strict: false, allErrors: true }).compile(schema)

type Nullability<T> = { [K in keyof T]-?: null extends T[K] ? true : false }

// Keys are the schema's names (ClarifyAnswers is RequestAnswers here).
const SHAPES = {
  ChatRequest: { message: false, thread_id: true, answers: true } satisfies Nullability<ChatRequest>,
  ClarifyAnswers: { choices: false, sides: false, closed: false } satisfies Nullability<RequestAnswers>,
}

interface SchemaProperty {
  anyOf?: SchemaProperty[]
  type?: string
}
type SchemaObject = { properties: Record<string, SchemaProperty> }

function nullability(def: SchemaObject) {
  return Object.fromEntries(
    Object.entries(def.properties).map(([field, prop]) => [field, (prop.anyOf ?? []).some((o) => o.type === 'null')]),
  )
}

const chips = fixtures.fenceClarify.find((event): event is ChipsEvent => event.type === 'chips')!

function body(answers: RequestAnswers | null): ChatRequest {
  return { message: 'x', thread_id: '3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10', answers }
}

describe('chat request contract', () => {
  it('has the same objects, fields and nullability as the schema', () => {
    const defs = schema.$defs as unknown as Record<string, SchemaObject>
    expect({
      ChatRequest: nullability(schema as unknown as SchemaObject),
      ClarifyAnswers: nullability(defs.ClarifyAnswers),
    }).toEqual(SHAPES)
  })

  it('has the same side limit as the schema', () => {
    expect(schema.$defs.ClarifyAnswers.properties.sides.items.maximum).toBe(MAX_SIDE_M)
  })

  it('validates the requests the clarify form builds', () => {
    let open = pickCorners(chips, initialAnswers(chips), '2')
    open = pickOption({ ...open, sides: ['10', '5', '7,5'] }, 'color', 'antracit')
    const closed = { ...toggleClosed(chips, open), sides: ['10', '5', '10', '5'] }
    const straight = pickCorners(chips, open, '0')
    const unknown = pickCorners(chips, open, 'unknown')
    const requests = [body(null), ...[open, closed, straight, unknown].map((a) => body(requestAnswers(chips, a)))]
    for (const request of requests) {
      validate(request)
      expect(validate.errors ?? []).toEqual([])
    }
  })

  it('rejects side lengths as text, too long or not a number, and unknown fields', () => {
    const broken: unknown[] = [
      body({ choices: {}, sides: '10 + 5 + 10' as unknown as number[], closed: false }),
      body({ choices: {}, sides: [10, 350], closed: false }),
      body({ choices: {}, sides: [0], closed: false }),
      { ...body(null), threadId: null },
      body({ choices: {}, sides: [], closed: false, corners: 2 } as RequestAnswers),
    ]
    for (const request of broken) {
      expect(validate(request)).toBe(false)
    }
  })
})
