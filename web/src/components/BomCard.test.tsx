import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BomEvent, ChatEvent } from '../api/events'
import { fixtures } from '../api/fixtures'
import { copy } from '../copy'
import { BomCard, type BomCardProps } from './BomCard'

function bomOf(events: ChatEvent[]): BomEvent {
  const bom = events.find((event): event is BomEvent => event.type === 'bom')
  if (!bom) throw new Error('fixture has no bom event')
  return bom
}

function quantitiesOf(bom: BomEvent): Record<string, number> {
  return Object.fromEntries(bom.lines.map((line) => [line.item, line.quantity]))
}

function renderCard(bom: BomEvent, props: Partial<BomCardProps> = {}) {
  const onQuantityChange = vi.fn<(item: string, quantity: number) => void>()
  const onAddAll = vi.fn<() => void>()
  render(
    <BomCard
      bom={bom}
      quantities={quantitiesOf(bom)}
      pending={false}
      failed={false}
      variant="card"
      onQuantityChange={onQuantityChange}
      onAddAll={onAddAll}
      {...props}
    />,
  )
  return { card: screen.getByRole('region', { name: bom.title }), onQuantityChange, onAddAll }
}

function totalRow(card: HTMLElement): HTMLElement {
  return within(card).getByTestId('bom-total')
}

describe('BomCard', () => {
  it('shows each line with its unit price, quantity and line total from the event', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom))
    const panel = within(card).getByRole('listitem', { name: 'Panel 153 × 250 cm' })
    expect(panel).toHaveTextContent('53,00 KM / kom')
    expect(panel).toHaveTextContent('530,00')
    expect(within(panel).getByText('10')).toBeInTheDocument()
  })

  it('shows the total from the event when every line has a price', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom))
    expect(totalRow(card)).toHaveTextContent('913,10 KM')
    expect(totalRow(card)).not.toHaveTextContent('na upit')
  })

  // The grounding rule: the card never adds prices up. A total that doesn't
  // match the lines must still be shown as the event sent it.
  it('takes the displayed total from the event, never from the lines', () => {
    const bom = { ...bomOf(fixtures.fenceBom), total: '1.23' }
    const { card } = renderCard(bom)
    expect(totalRow(card)).toHaveTextContent('1,23 KM')
    expect(totalRow(card)).not.toHaveTextContent('913,10')
  })

  it('takes each line total from the event, not from unit price × quantity', () => {
    const bom = bomOf(fixtures.fenceBom)
    const odd = { ...bom, lines: bom.lines.map((line) => (line.item === 'panel' ? { ...line, line_total: '7.77' } : line)) }
    const { card } = renderCard(odd)
    const panel = within(card).getByRole('listitem', { name: 'Panel 153 × 250 cm' })
    expect(panel).toHaveTextContent('7,77')
    expect(panel).not.toHaveTextContent('530,00')
  })

  it('shows the priced sum and how many lines are "na upit" when some lines have no price', () => {
    const { card } = renderCard(bomOf(fixtures.rollFenceSomeOnRequest))
    expect(totalRow(card)).toHaveTextContent('292,55 KM')
    expect(totalRow(card)).toHaveTextContent('+ 1 stavka na upit')
    const brace = within(card).getByRole('listitem', { name: 'Potporni stup 100 cm' })
    expect(brace).toHaveTextContent(copy.chat.bom.onRequest)
    expect(brace).not.toHaveTextContent('KM')
  })

  it('shows "cijena na upit" instead of a total when no line has a price, and the button stays enabled', () => {
    const { card } = renderCard(bomOf(fixtures.bomAllOnRequest))
    expect(totalRow(card)).toHaveTextContent(copy.chat.bom.totalOnRequest)
    expect(totalRow(card)).not.toHaveTextContent('KM')
    expect(totalRow(card)).not.toHaveTextContent('0,00')
    expect(within(card).getByRole('button', { name: copy.chat.bom.addAll })).toBeEnabled()
  })

  it('shows extra lines with their note, without a price or a stepper', () => {
    const { card } = renderCard(bomOf(fixtures.fenceConcretePosts))
    const extra = within(card).getByRole('listitem', { name: 'Stupovi za betoniranje – po upitu' })
    expect(extra).toHaveTextContent('Rade se po narudžbi, 10–15 dana.')
    expect(within(extra).queryByRole('button')).not.toBeInTheDocument()
    expect(extra).not.toHaveTextContent('KM')
  })

  it('shows the notes from the event on the card, and the canvas footnote instead on the panel', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom))
    expect(card).toHaveTextContent('Cijene i ponuda su informativne')
    expect(card).not.toHaveTextContent(copy.chat.bom.panelFootnote)
  })

  it('shows the canvas footnote, not the event notes, on the wide panel', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom), { variant: 'panel' })
    expect(card).toHaveTextContent(copy.chat.bom.panelFootnote)
    expect(card).not.toHaveTextContent('Cijene i ponuda su informativne')
  })

  it('asks for one more or one less through onQuantityChange', () => {
    const { card, onQuantityChange } = renderCard(bomOf(fixtures.fenceBom))
    const panel = within(card).getByRole('listitem', { name: 'Panel 153 × 250 cm' })
    fireEvent.click(within(panel).getByRole('button', { name: copy.chat.bom.more('Panel 153 × 250 cm') }))
    expect(onQuantityChange).toHaveBeenLastCalledWith('panel', 11)
    fireEvent.click(within(panel).getByRole('button', { name: copy.chat.bom.less('Panel 153 × 250 cm') }))
    expect(onQuantityChange).toHaveBeenLastCalledWith('panel', 9)
  })

  it('shows the quantities it is given and cannot go below 0', () => {
    const bom = bomOf(fixtures.fenceBom)
    const { card } = renderCard(bom, { quantities: { ...quantitiesOf(bom), panel: 0 } })
    const panel = within(card).getByRole('listitem', { name: 'Panel 153 × 250 cm' })
    expect(within(panel).getByText('0')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: copy.chat.bom.less('Panel 153 × 250 cm') })).toBeDisabled()
  })

  it('marks the totals busy and holds "Dodaj sve u korpu" while recalculating', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom), { pending: true })
    expect(totalRow(card)).toHaveAttribute('aria-busy', 'true')
    expect(within(card).getByRole('button', { name: copy.chat.bom.addAll })).toBeDisabled()
  })

  it('says so when the recalculation failed', () => {
    const { card } = renderCard(bomOf(fixtures.fenceBom), { failed: true })
    expect(within(card).getByRole('alert')).toHaveTextContent(copy.chat.bom.failed)
  })

  it('calls onAddAll from the red button', () => {
    const { card, onAddAll } = renderCard(bomOf(fixtures.fenceBom))
    fireEvent.click(within(card).getByRole('button', { name: copy.chat.bom.addAll }))
    expect(onAddAll).toHaveBeenCalledOnce()
  })

  it('uses the canvas total wording for each layout', () => {
    const bom = bomOf(fixtures.fenceBom)
    const { card } = renderCard(bom)
    expect(totalRow(card)).toHaveTextContent(copy.chat.bom.totalLabel.card)
    expect(totalRow(card)).toHaveTextContent(copy.chat.bom.afterTotal)
  })
})
