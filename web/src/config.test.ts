import { describe, expect, it } from 'vitest'
import { readConfig } from './config'

describe('readConfig', () => {
  it("uses Martom's public shop number when nothing is set", () => {
    expect(readConfig({}).shopPhone).toBe('036 651 427')
  })

  it('takes the shop number from VITE_SHOP_PHONE', () => {
    expect(readConfig({ VITE_SHOP_PHONE: '036 000 000' }).shopPhone).toBe('036 000 000')
  })

  it('ignores a blank VITE_SHOP_PHONE', () => {
    expect(readConfig({ VITE_SHOP_PHONE: '  ' }).shopPhone).toBe('036 651 427')
  })

  it('leaves LangSmith off unless VITE_LANGSMITH_ENABLED is true', () => {
    expect(readConfig({}).langSmithEnabled).toBe(false)
    expect(readConfig({ VITE_LANGSMITH_ENABLED: 'false' }).langSmithEnabled).toBe(false)
    expect(readConfig({ VITE_LANGSMITH_ENABLED: '1' }).langSmithEnabled).toBe(false)
    expect(readConfig({ VITE_LANGSMITH_ENABLED: 'true' }).langSmithEnabled).toBe(true)
    expect(readConfig({ VITE_LANGSMITH_ENABLED: ' TRUE ' }).langSmithEnabled).toBe(true)
  })
})
