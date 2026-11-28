import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { config } from '../config'
import { copy, privacyPlaceholders, telHref } from '../copy'
import { PrivacyPage } from './PrivacyPage'

// The canvas text, as synced into docs/mockups (canvas 1795851325-40ff).
const mockup = (variant: 'web' | 'mobile') =>
  readFileSync(join(import.meta.dirname, '../../../docs/mockups', variant, '6-privatnost.html'), 'utf8')

const MOCKUP_PHONE = '036 651 427'

/**
 * The visible text of a mockup's <main>: the indentation between tags and the
 * tags themselves dropped (as the DOM's textContent does), spaces collapsed.
 */
function mockupMainText(variant: 'web' | 'mobile'): string {
  const main = mockup(variant).match(/<main[^>]*>([\s\S]*?)<\/main>/)?.[1] ?? ''
  const text = collapse(main.replace(/>\s+</g, '><').replace(/<[^>]+>/g, ''))
  // The page shows the configured shop number (VITE_SHOP_PHONE), the mockup
  // Martom's public one.
  return text.replaceAll(MOCKUP_PHONE, config.shopPhone)
}

function collapse(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

describe('PrivacyPage', () => {
  it('shows the page title, every section heading and the intro', () => {
    render(<PrivacyPage />)
    expect(screen.getByRole('heading', { level: 1, name: copy.privacyPage.title })).toBeInTheDocument()
    for (const section of copy.privacyPage.sections) {
      expect(screen.getByRole('heading', { level: 2, name: section.heading })).toBeInTheDocument()
    }
    expect(screen.getByText(copy.privacyPage.intro)).toHaveClass('privacy-page__intro--wide')
    expect(screen.getByText(copy.privacyPage.introShort)).toHaveClass('privacy-page__intro--narrow')
  })

  // The canvas is the source of truth: the page says exactly what the
  // mockups say, on wide screens and on phones.
  it.each(['web', 'mobile'] as const)('renders the %s mockup text word for word', (variant) => {
    render(<PrivacyPage />)
    const main = screen.getByRole('main')
    // Drop the other variant's intro, which is hidden by CSS.
    const hidden = variant === 'web' ? '.privacy-page__intro--narrow' : '.privacy-page__intro--wide'
    main.querySelector(hidden)?.remove()
    expect(collapse(main.textContent ?? '')).toBe(mockupMainText(variant))
  })

  // PLACEHOLDERS: these must stay until they are confirmed. When Toni gives
  // the date and the order retention period, or the contracts confirm the
  // transfer safeguard, change them on the canvas and in copy.ts
  // (privacyPlaceholders), then update this test.
  it('still shows the placeholders Martom has to confirm', () => {
    render(<PrivacyPage />)
    const main = screen.getByRole('main')
    expect(main).toHaveTextContent(privacyPlaceholders.date)
    expect(main).toHaveTextContent(privacyPlaceholders.orderRetention)
    expect(main).toHaveTextContent(privacyPlaceholders.transferSafeguard)
    expect(privacyPlaceholders).toEqual({
      date: '[DATUM]',
      orderRetention: '[ROK ČUVANJA – potvrđuje Martom, npr. koliko zahtijevaju računovodstveni propisi]',
      transferSafeguard: '[ZAŠTITA PRIJENOSA – provjeriti ugovore]',
    })
  })

  it('links back to the advisor on wide screens and on phones', () => {
    render(<PrivacyPage />)
    expect(screen.getByRole('link', { name: copy.privacyPage.back })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: copy.privacyPage.backShort })).toHaveAttribute('href', '/')
  })

  it('links the e-mail and the shop phone', () => {
    render(<PrivacyPage />)
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: copy.shop.email })).toHaveAttribute(
      'href',
      `mailto:${copy.shop.email}`,
    )
    const footer = screen.getByRole('contentinfo')
    for (const link of within(footer).getAllByRole('link', { name: copy.shop.phone })) {
      expect(link).toHaveAttribute('href', telHref(copy.shop.phone))
    }
  })

  it('sets the browser tab title while open and restores it after', () => {
    document.title = 'Martom savjetnik'
    const { unmount } = render(<PrivacyPage />)
    expect(document.title).toBe(copy.privacyPage.documentTitle)
    unmount()
    expect(document.title).toBe('Martom savjetnik')
  })
})
