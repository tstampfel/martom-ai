import { useState } from 'react'
import type { ProductCard as Card, Variant } from '../api/events'
import { formatPrice } from '../clarify'
import { copy } from '../copy'

/** The products of one reply (3-results.html). The first card is the recommendation. */
export function ProductCards({ cards }: { cards: Card[] }) {
  return (
    <ul className="product-cards" aria-label={copy.chat.cards.label}>
      {cards.map((card, index) => (
        <li key={card.listing_id}>
          <ProductCard card={card} featured={index === 0} />
        </li>
      ))}
    </ul>
  )
}

/** The variant shown first: the one whose price the card shows, else the first. */
function initialVariant(card: Card): number {
  const index = card.variants.findIndex((variant) => variant.price === card.price)
  return index === -1 ? 0 : index
}

function ProductCard({ card, featured }: { card: Card; featured: boolean }) {
  const [picked, setPicked] = useState(() => initialVariant(card))
  const variant: Variant | undefined = card.variants[picked]
  const price = variant ? variant.price : card.price

  return (
    <article className={featured ? 'product-card product-card--featured' : 'product-card'}>
      <div className="product-card__photo">
        {card.photo_url ? (
          <img src={card.photo_url} alt="" loading="lazy" />
        ) : (
          <span className="product-card__placeholder">{copy.chat.cards.noPhoto}</span>
        )}
        {card.badge ? <span className="product-card__badge">{card.badge}</span> : null}
      </div>
      <div className="product-card__body">
        <h3 className="product-card__title">{card.title}</h3>
        {card.subtitle ? <span className="product-card__subtitle">{card.subtitle}</span> : null}
        <p className="product-card__why">{card.why}</p>
        {card.variants.length > 0 ? (
          <fieldset className="product-card__variants">
            <legend className="visually-hidden">{copy.chat.cards.variants}</legend>
            {card.variants.map((option, index) => (
              <button
                key={option.label}
                type="button"
                className={index === picked ? 'variant variant--on' : 'variant'}
                aria-pressed={index === picked}
                onClick={() => setPicked(index)}
              >
                {option.label}
              </button>
            ))}
          </fieldset>
        ) : null}
        <div className="product-card__foot">
          {price === null ? (
            <span className="product-card__price product-card__price--request">{copy.chat.cards.onRequest}</span>
          ) : (
            <span className="product-card__price">
              {formatPrice(price)}
              {card.price_unit ? (
                <span className="product-card__unit"> {copy.chat.cards.perUnit(card.price_unit)}</span>
              ) : null}
            </span>
          )}
          <a
            className="product-card__olx"
            href={card.url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={copy.chat.cards.olxLabel(card.title)}
          >
            {copy.chat.cards.olx}
          </a>
        </div>
      </div>
    </article>
  )
}
