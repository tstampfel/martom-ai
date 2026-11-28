import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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

  // "2.600+" was the duplicated OLX row count; until the database can count
  // the unique listings, the hero names the catalogue without a number.
  it('names the OLX catalogue in the hero lead without an article count', () => {
    expect(copy.hero.lead).toContain('iz cijelog Martomovog OLX kataloga')
    expect(copy.hero.lead).not.toMatch(/\d/)
  })

  // The start page says "kopče", as the design canvas does (#76). BOM line
  // names ("Obujmica PVC …") come from the products and are not start copy.
  it('says "kopče", not "obujmice", on the start page', () => {
    const startCopy = JSON.stringify([copy.hero, copy.start, copy.agentCard, copy.howItWorks])
    expect(startCopy).not.toMatch(/obujmic/i)
    expect(copy.hero.lead).toContain('Panele, stupove, kopče i žicu')
  })

  // Canvas wording from docs/mockups/{web,mobile}/1-start.html; the phone
  // mockup shows the full step text, the same as wide screens.
  it('uses the canvas title and steps on the agent card', () => {
    expect(copy.agentCard.title).toBe('Kako savjetnik radi')
    const canvasSteps = [
      'Razumijem: 25 m · dvorište · 150 cm',
      'Pronašao panele, stupove i pribor',
      'Gotov popis: 905,10 KM okvirno',
    ]
    expect(copy.agentCard.steps.map((step) => step.text)).toEqual(canvasSteps)
  })

  // Canvas version 1795793205-4582 (Tomislav, 27 Nov 2026), chat notices from
  // 1795855309-7fee ("lične"): docs/mockups/web
  // and mobile 1-start.html (footer) and 4-cart-order.html (order form).
  it('uses the canvas wording for the privacy texts', () => {
    expect(copy.privacy.chatNotice).toBe(
      'Razgovore sa savjetnikom čuvamo 90 dana kako bismo poboljšali odgovore, zatim se brišu. Ne upisujte lične podatke u razgovor.',
    )
    expect(copy.privacy.chatNoticeShort).toBe(
      'Razgovore čuvamo 90 dana radi poboljšanja savjetnika. Ne upisujte lične podatke.',
    )
    expect(copy.privacy.orderNotice).toBe('Ime, telefon i adresu koristimo samo za ovu narudžbu.')
    expect(copy.privacy.link).toBe('Privatnost')
  })

  // Bosnian plural: 1 stavka, 2–4 stavke, 5+ stavki (21 stavka, 11–14 stavki).
  it('counts the "na upit" lines under the total with the right plural', () => {
    const { unpriced } = copy.chat.bom
    expect(unpriced(1)).toBe('+ 1 stavka na upit')
    expect(unpriced(2)).toBe('+ 2 stavke na upit')
    expect(unpriced(4)).toBe('+ 4 stavke na upit')
    expect(unpriced(5)).toBe('+ 5 stavki na upit')
    expect(unpriced(11)).toBe('+ 11 stavki na upit')
    expect(unpriced(12)).toBe('+ 12 stavki na upit')
    expect(unpriced(21)).toBe('+ 21 stavka na upit')
    expect(unpriced(22)).toBe('+ 22 stavke na upit')
  })

  // Canvas version 1795853807-d861 (Tomislav, 28 Nov 2026, #151, EU AI Act
  // Art. 50): docs/mockups/{web,mobile}/1-start.html and 2-clarify.html.
  it('uses the canvas wording for the AI notice', () => {
    const { start, chat } = copy.aiNotice
    expect(start.wide).toEqual({
      lead: 'Odgovara AI savjetnik',
      rest: ' (umjetna inteligencija), ne osoba. Cijene i količine potvrđuje Martom.',
    })
    expect(start.short).toEqual({ lead: 'Odgovara AI savjetnik', rest: ', ne osoba. Cijene potvrđuje Martom.' })
    expect(chat.wide).toEqual({
      lead: 'Razgovarate s AI savjetnikom',
      rest: ' (umjetna inteligencija), ne s osobom. Može pogriješiti, a cijene i količine potvrđuje Martom prije narudžbe.',
    })
    expect(chat.short).toEqual({
      lead: 'Razgovarate s AI savjetnikom',
      rest: ', ne s osobom. Može pogriješiti, cijene i količine potvrđuje Martom.',
    })
  })
})

describe('copy.footer credit (M5-9)', () => {
  // Canvas version 1795853475-32a4: the four screens with the credit.
  it.each(['web/1-start.html', 'mobile/1-start.html', 'web/6-privatnost.html', 'mobile/6-privatnost.html'])(
    'matches the credit in docs/mockups/%s',
    (screen) => {
      const html = readFileSync(join(import.meta.dirname, '../../docs/mockups', screen), 'utf8')
      // Every new-tab link with its lead-in (the Privatnost page also links azlp.ba).
      const links = [
        ...html.matchAll(/(?:>| · )([^<>·]+) <a href="([^"]+)" target="_blank" rel="noopener"[^>]*>([^<]+)<\/a>/g),
      ].map((match) => match.slice(1))
      expect(links).toContainEqual([copy.footer.creditLead, copy.footer.creditUrl, copy.footer.credit])
    },
  )
})

describe('copy.about OLX badge', () => {
  // Canvas version 1795853807-d861: a fixed count, never computed live.
  it('matches the OLX tile in docs/mockups/web/1-start.html', () => {
    const html = readFileSync(join(import.meta.dirname, '../../docs/mockups/web/1-start.html'), 'utf8')
    const tile = html.match(/>OLX<\/div><span[^>]*>([^<]+)<\/span>/)?.[1]
    const olx = copy.about.badges.find((badge) => badge.value === 'OLX')
    expect(olx?.text).toBe(tile)
    expect(tile).toBe('Preko 2.500 artikala online na OLX-u, savjetnik pretražuje sve')
  })
})
