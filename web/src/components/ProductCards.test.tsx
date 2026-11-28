import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChatEvent, ProductCard } from '../api/events'
import { fixtures } from '../api/fixtures'
import { copy } from '../copy'
import { ProductCards } from './ProductCards'

function cardsOf(events: ChatEvent[]): ProductCard[] {
  return events.flatMap((event) => (event.type === 'product_cards' ? event.cards : []))
}

describe('ProductCards', () => {
  it('links an OLX product to its listing', () => {
    const [card] = cardsOf(fixtures.productQuestion)
    render(<ProductCards cards={[card]} />)
    expect(screen.getByRole('link', { name: copy.chat.cards.olxLabel(card.title) })).toHaveAttribute('href', card.url)
  })

  it('shows a manual product with its price and no OLX link', () => {
    const [card] = cardsOf(fixtures.manualProduct)
    render(<ProductCards cards={[card]} />)
    expect(screen.getByRole('heading', { name: 'Anker M8×70' })).toBeInTheDocument()
    expect(screen.getByText(/0,80/)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByText(copy.chat.cards.olx)).toBeNull()
    expect(screen.queryByText(copy.chat.cards.noPhoto)).toBeNull()
  })

  it('shows a manual product without a photo as a grey placeholder with an icon', () => {
    // Tomislav (#132): no "Bez slike" text; screen readers hear "Slika nije dostupna".
    const [card] = cardsOf(fixtures.manualProduct)
    render(<ProductCards cards={[card]} />)
    const hidden = screen.getByText(copy.chat.cards.photoUnavailable)
    expect(hidden).toHaveClass('visually-hidden')
    const placeholder = hidden.closest('.product-card__photo--manual')
    expect(placeholder).not.toBeNull()
    expect(placeholder?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    // The only text is the screen-reader name; nothing visible.
    expect(placeholder?.textContent).toBe(copy.chat.cards.photoUnavailable)
    expect(screen.queryByText('Bez slike')).toBeNull()
  })

  it('renders an OLX and a manual product side by side', () => {
    const cards = [...cardsOf(fixtures.productQuestion), ...cardsOf(fixtures.manualProduct)]
    render(<ProductCards cards={cards} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })
})
