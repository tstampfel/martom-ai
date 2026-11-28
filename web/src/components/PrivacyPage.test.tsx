import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { config } from '../config'
import { copy, privacyPlaceholders, telHref } from '../copy'
import { PrivacyPage } from './PrivacyPage'

// The canvas text, as synced into docs/mockups (canvas 1795856792-66d8).
const mockup = (variant: 'web' | 'mobile') =>
  readFileSync(join(import.meta.dirname, '../../../docs/mockups', variant, '6-privatnost.html'), 'utf8')

const MOCKUP_PHONE = '036 651 427'

// The canvas marks the LangSmith sentence with an HTML comment: the comment
// and the sentence after it, up to its full stop and the following space.
const LANGSMITH_SENTENCE = /<!--[^>]*LangSmith[^>]*-->[^.]*\.\s*/

/**
 * The visible text of a mockup's <main>: the indentation between tags and the
 * tags themselves dropped (as the DOM's textContent does), spaces collapsed.
 * Without LangSmith, the marked sentence is left out.
 */
function mockupMainText(variant: 'web' | 'mobile', { langSmith }: { langSmith: boolean }): string {
  let main = mockup(variant).match(/<main[^>]*>([\s\S]*?)<\/main>/)?.[1] ?? ''
  expect(main).toMatch(LANGSMITH_SENTENCE)
  if (!langSmith) main = main.replace(LANGSMITH_SENTENCE, '')
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
  // mockups say, on wide screens and on phones, with the LangSmith sentence
  // only when LangSmith is on.
  describe.each([true, false])('with LangSmith %s', (langSmith) => {
    it.each(['web', 'mobile'] as const)('renders the %s mockup text word for word', (variant) => {
      render(<PrivacyPage langSmith={langSmith} />)
      const main = screen.getByRole('main')
      // Drop the other variant's intro, which is hidden by CSS.
      const hidden = variant === 'web' ? '.privacy-page__intro--narrow' : '.privacy-page__intro--wide'
      main.querySelector(hidden)?.remove()
      expect(collapse(main.textContent ?? '')).toBe(mockupMainText(variant, { langSmith }))
    })
  })

  it('names LangSmith only when it is on', () => {
    const { unmount } = render(<PrivacyPage langSmith />)
    expect(screen.getByRole('main')).toHaveTextContent('LangSmith')
    unmount()
    render(<PrivacyPage langSmith={false} />)
    expect(screen.getByRole('main')).not.toHaveTextContent('LangSmith')
  })

  it('follows VITE_LANGSMITH_ENABLED by default', () => {
    render(<PrivacyPage />)
    const shown = (screen.getByRole('main').textContent ?? '').includes('LangSmith')
    expect(shown).toBe(config.langSmithEnabled)
  })

  // PLACEHOLDERS: these must stay until they are confirmed. When Tomislav
  // sets the date and Toni gives the order retention period, change them on
  // the canvas and in copy.ts (privacyPlaceholders), then update this test.
  it('still shows the placeholders Martom has to confirm', () => {
    render(<PrivacyPage />)
    const main = screen.getByRole('main')
    expect(main).toHaveTextContent(privacyPlaceholders.date)
    expect(main).toHaveTextContent(privacyPlaceholders.orderRetention)
    expect(privacyPlaceholders).toEqual({
      date: '[DATUM]',
      orderRetention: '[ROK ČUVANJA – potvrđuje Martom]',
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
    expect(within(main).getByRole('link', { name: copy.shop.phone })).toHaveAttribute(
      'href',
      telHref(copy.shop.phone),
    )
    // Martom's public number, as on the canvas.
    expect(telHref('036 651 427')).toBe('tel:+38736651427')
    const footer = screen.getByRole('contentinfo')
    for (const link of within(footer).getAllByRole('link', { name: copy.shop.phone })) {
      expect(link).toHaveAttribute('href', telHref(copy.shop.phone))
    }
  })

  it('links the data protection agency in a new tab', () => {
    render(<PrivacyPage />)
    const link = within(screen.getByRole('main')).getByRole('link', { name: 'www.azlp.ba' })
    expect(link).toHaveAttribute('href', 'https://www.azlp.ba')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener')
  })

  it('sets the browser tab title while open and restores it after', () => {
    document.title = 'Martom savjetnik'
    const { unmount } = render(<PrivacyPage />)
    expect(document.title).toBe(copy.privacyPage.documentTitle)
    unmount()
    expect(document.title).toBe('Martom savjetnik')
  })
})
