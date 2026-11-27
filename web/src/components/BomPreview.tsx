import type { BomEvent } from '../api/events'
import { copy } from '../copy'

/**
 * A plain view of the bom event: title, summary, lines with quantities and
 * the no-price extra lines. Prices, totals, steppers and "Dodaj sve u
 * korpu" come with the materials list card in #74, which replaces this.
 */
export function BomPreview({ bom }: { bom: BomEvent }) {
  return (
    <section className="bom-preview" aria-label={bom.title}>
      <h3 className="bom-preview__title">{bom.title}</h3>
      <span className="bom-preview__summary">{bom.summary}</span>
      <ul className="bom-preview__lines">
        {bom.lines.map((line) => (
          <li key={line.item}>
            <span>{line.name}</span>
            <span className="bom-preview__qty">{copy.chat.bom.quantity(line.quantity)}</span>
          </li>
        ))}
        {bom.extra_lines.map((line) => (
          <li key={line.name}>
            <span>{line.name}</span>
            {line.note ? <span className="bom-preview__note">{line.note}</span> : null}
          </li>
        ))}
      </ul>
      {bom.notes.map((note) => (
        <p key={note} className="bom-preview__note">
          {note}
        </p>
      ))}
    </section>
  )
}
