import { describe, expect, it } from 'vitest'
import { copy, telHref } from './copy'

describe('telHref', () => {
  it('turns a local BiH number into an international tel: link', () => {
    expect(telHref('036 651 427')).toBe('tel:+38736651427')
  })

  it('keeps a number that is already international', () => {
    expect(telHref('+387 36 651 427')).toBe('tel:+38736651427')
  })
})

describe('copy', () => {
  it('uses the public shop number from the mockups', () => {
    expect(copy.shop.phone).toBe('036 651 427')
  })

  it('formats the cart label with the item count', () => {
    expect(copy.header.cart(0)).toBe('Korpa · 0')
    expect(copy.header.cart(3)).toBe('Korpa · 3')
  })
})
