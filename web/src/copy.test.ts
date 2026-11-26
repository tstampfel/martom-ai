import { describe, expect, it } from 'vitest'
import { config } from './config'
import { copy, telHref } from './copy'

describe('telHref', () => {
  it('turns a local BiH number into an international tel: link', () => {
    expect(telHref('036 651 427')).toBe('tel:+38736651427')
  })

  it('keeps a number that is already international', () => {
    expect(telHref('+387 36 651 427')).toBe('tel:+38736651427')
  })

  it('turns a 00 international prefix into +', () => {
    expect(telHref('00387 36 651 427')).toBe('tel:+38736651427')
  })
})

describe('copy', () => {
  // The default (the public number) is checked in config.test.ts.
  it('uses the shop number from config', () => {
    expect(copy.shop.phone).toBe(config.shopPhone)
  })

  it('formats the cart label with the item count', () => {
    expect(copy.header.cart(0)).toBe('Korpa · 0')
    expect(copy.header.cart(3)).toBe('Korpa · 3')
  })
})
