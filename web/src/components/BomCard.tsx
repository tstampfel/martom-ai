import type { BomEvent, BomLine, ExtraLine } from '../api/events'
import { formatAmount, formatPrice } from '../clarify'
import { copy } from '../copy'

export type BomCardProps = {
  /** The latest bom event: every price and total shown comes from it. */
  bom: BomEvent
  /** BOM item key → the quantity on the stepper (may be ahead of `bom` while recalculating). */
  quantities: Record<string, number>
  /** A recalculation is on its way; the shown totals are the previous ones. */
  pending: boolean
  /** The last recalculation failed; the steppers are back on `bom`'s quantities. */
  failed: boolean
  /** card: white card in the conversation (phones). panel: charcoal panel beside it (wide). */
  variant: 'card' | 'panel'
  onQuantityChange: (item: string, quantity: number) => void
  onAddAll: () => void
}

/**
 * The materials list (popis materijala) of 3-results.html: lines with +/−
 * steppers, the total and "Dodaj sve u korpu". It only displays the bom
 * event: it never adds or multiplies prices (grounding rule). A stepper
 * calls onQuantityChange, and the parent sends the new quantities to the
 * recalculation interface and passes the bom event that comes back.
 */
export function BomCard({ bom, quantities, pending, failed, variant, onQuantityChange, onAddAll }: BomCardProps) {
  const text = copy.chat.bom

  return (
    <section className={`bom-card bom-card--${variant}`} aria-label={bom.title}>
      <div className="bom-card__head">
        <h2 className="bom-card__title">{bom.title}</h2>
        <span className="bom-card__summary">{bom.summary}</span>
      </div>
      <ul className="bom-card__lines">
        {bom.lines.map((line) => (
          <LineRow
            key={line.item}
            line={line}
            quantity={quantities[line.item] ?? line.quantity}
            onQuantityChange={onQuantityChange}
          />
        ))}
        {bom.extra_lines.map((line) => (
          <ExtraRow key={line.name} line={line} />
        ))}
      </ul>
      <div className="bom-card__total" data-testid="bom-total" aria-busy={pending}>
        <div className="bom-card__total-row">
          <span className="bom-card__total-label">{text.totalLabel[variant]}</span>
          <span className="bom-card__total-value">
            {bom.total === null ? text.totalOnRequest : formatPrice(bom.total)}
            {variant === 'card' && bom.total !== null ? (
              <span className="bom-card__after-total"> {text.afterTotal}</span>
            ) : null}
          </span>
        </div>
        {bom.total !== null && bom.unpriced_count > 0 ? (
          <span className="bom-card__unpriced">{text.unpriced(bom.unpriced_count)}</span>
        ) : null}
        {variant === 'panel' ? <span className="bom-card__footnote">{text.panelFootnote}</span> : null}
      </div>
      <output className="visually-hidden">{pending ? text.updating : ''}</output>
      {failed ? (
        <p className="bom-card__error" role="alert">
          {text.failed}
        </p>
      ) : null}
      {/* The wide panel shows the canvas footnote instead (web/3-results.html). */}
      {variant === 'card'
        ? bom.notes.map((note) => (
            <p key={note} className="bom-card__note">
              {note}
            </p>
          ))
        : null}
      <button type="button" className="bom-card__add" onClick={onAddAll} disabled={pending}>
        {text.addAll}
      </button>
    </section>
  )
}

function LineRow({
  line,
  quantity,
  onQuantityChange,
}: {
  line: BomLine
  quantity: number
  onQuantityChange: (item: string, quantity: number) => void
}) {
  const text = copy.chat.bom
  const price = line.unit_price === null ? text.onRequest : text.perUnit(formatPrice(line.unit_price))
  const detail = [price, line.label].filter(Boolean).join(' · ')

  return (
    <li className="bom-card__line" aria-label={line.name}>
      <div className="bom-card__line-text">
        <span className="bom-card__line-name">{line.name}</span>
        <span className="bom-card__line-detail">{detail}</span>
      </div>
      <div className="bom-card__stepper">
        <button
          type="button"
          aria-label={text.less(line.name)}
          disabled={quantity <= 0}
          onClick={() => onQuantityChange(line.item, quantity - 1)}
        >
          −
        </button>
        <output aria-label={text.quantity(line.name)}>{quantity}</output>
        <button type="button" aria-label={text.more(line.name)} onClick={() => onQuantityChange(line.item, quantity + 1)}>
          +
        </button>
      </div>
      <span className="bom-card__line-total">{line.line_total === null ? '—' : formatAmount(line.line_total)}</span>
    </li>
  )
}

/** A no-price order line (e.g. concrete posts): no stepper, no price. */
function ExtraRow({ line }: { line: ExtraLine }) {
  return (
    <li className="bom-card__line bom-card__line--extra" aria-label={line.name}>
      <div className="bom-card__line-text">
        <span className="bom-card__line-name">{line.name}</span>
        {line.note ? <span className="bom-card__line-detail">{line.note}</span> : null}
      </div>
    </li>
  )
}
