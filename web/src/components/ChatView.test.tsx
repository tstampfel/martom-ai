import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChatClient, ChatRequest } from '../api/chatClient'
import { CHAT_EVENT_TYPES } from '../api/events'
import { createFakeBomRecalculator } from '../api/fakeBomRecalculator'
import { createFakeChatClient } from '../api/fakeChatClient'
import { fixtures } from '../api/fixtures'
import { copy } from '../copy'
import { ChatView } from './ChatView'

const noWait = () => Promise.resolve()

/** A fake client that records every request and replays the default fixtures at once. */
function recordingClient(pickReply?: (request: ChatRequest) => readonly unknown[]) {
  const requests: ChatRequest[] = []
  const inner = createFakeChatClient({ wait: noWait, pickReply })
  const client: ChatClient = {
    stream(request, signal) {
      requests.push(request)
      return inner.stream(request, signal)
    },
  }
  return { client, requests }
}

function renderChat(firstMessage: string, client: ChatClient) {
  const recalculator = createFakeBomRecalculator({ wait: noWait })
  return render(<ChatView firstMessage={firstMessage} client={client} recalculator={recalculator} onBack={() => {}} />)
}

const FENCE = 'Treba mi 25 m ograde za dvorište, visina oko 1,5 m'

async function answerClarify() {
  const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
  fireEvent.click(within(corners).getByRole('button', { name: '2' }))
  const sides = screen.getByRole('group', { name: 'Dužina svake strane (m)' })
  const inputs = within(sides).getAllByRole('textbox')
  expect(inputs).toHaveLength(3)
  ;['10', '5', '10'].forEach((value, index) => fireEvent.change(inputs[index], { target: { value } }))
  fireEvent.click(within(screen.getByRole('group', { name: 'Gdje idu stupovi?' })).getByRole('button', { name: /pločicom/ }))
  fireEvent.click(within(screen.getByRole('group', { name: 'Koja boja?' })).getByRole('button', { name: /Antracit/ }))
}

describe('ChatView', () => {
  it('shows the first message and streams the reply: steps, text and clarify questions', async () => {
    const { client, requests } = recordingClient()
    renderChat(FENCE, client)
    expect(screen.getByText(FENCE)).toBeInTheDocument()
    expect(await screen.findByText(/Odlično — za 25 m dvorišta imamo dobra rješenja\./)).toBeInTheDocument()
    const steps = screen.getByRole('list', { name: copy.chat.steps.label })
    expect(within(steps).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      expect.stringContaining('Razumio: 25 m · dvorište · 150 cm'),
      expect.stringContaining('Pregledao 39 ograda'),
      expect.stringContaining('Trebam još 3 podatka'),
    ])
    expect(screen.getByRole('group', { name: 'Koja boja?' })).toBeInTheDocument()
    expect(requests).toEqual([{ message: FENCE, threadId: null, answers: null }])
  })

  it('does not replay the first message when the client changes', async () => {
    const first = recordingClient(() => fixtures.productQuestion)
    const { rerender } = renderChat('Imate li dugi katanac?', first.client)
    await screen.findByRole('heading', { name: 'Katanac dugi 50/40 i 30 mm' })
    const second = recordingClient(() => fixtures.productQuestion)
    rerender(
      <ChatView
        firstMessage="Imate li dugi katanac?"
        client={second.client}
        recalculator={createFakeBomRecalculator({ wait: noWait })}
        onBack={() => {}}
      />,
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(second.requests).toEqual([])
    expect(screen.getAllByRole('heading', { name: 'Katanac dugi 50/40 i 30 mm' })).toHaveLength(1)
  })

  it('shows the "uživo" dot only while the reply streams', async () => {
    let finish: () => void = () => {}
    const finished = new Promise<void>((resolve) => (finish = resolve))
    const client: ChatClient = {
      async *stream() {
        yield { type: 'step', id: 'parse', text: 'Razumio: dugi katanac', status: 'working' }
        await finished
        yield { type: 'done', thread_id: 't-1' }
      },
    }
    renderChat('Imate li dugi katanac?', client)
    expect(await screen.findByText(copy.chat.steps.live)).toBeInTheDocument()
    finish()
    await screen.findByLabelText(copy.chat.composer.label)
    await expect.poll(() => screen.queryByText(copy.chat.steps.live)).toBeNull()
  })

  it('shows the badge on a chip and the note once that option is picked', async () => {
    renderChat(FENCE, recordingClient().client)
    const posts = await screen.findByRole('group', { name: 'Gdje idu stupovi?' })
    const concrete = within(posts).getByRole('button', { name: /U zemlju \(betoniram\)/ })
    expect(concrete).toHaveTextContent('po upitu · po narudžbi')
    expect(screen.queryByText(/Beton ne prodajemo/)).not.toBeInTheDocument()
    fireEvent.click(concrete)
    expect(concrete).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/Beton ne prodajemo/)).toBeInTheDocument()
  })

  it('fills in the "Vaš projekt" panel as the customer answers', async () => {
    renderChat(FENCE, recordingClient().client)
    await answerClarify()
    const panel = screen.getByRole('complementary', { name: copy.chat.project.label })
    expect(panel).toHaveTextContent('25 m')
    expect(panel).toHaveTextContent('Na beton, s pločicom')
    expect(panel).toHaveTextContent('Antracit (RAL 7016)')
  })

  it('keeps every answer when several chips are picked before a re-render', async () => {
    renderChat(FENCE, recordingClient().client)
    const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
    const color = screen.getByRole('group', { name: 'Koja boja?' })
    act(() => {
      within(corners).getByRole('button', { name: '0' }).click()
      within(color).getByRole('button', { name: /Zelena/ }).click()
    })
    expect(within(corners).getByRole('button', { name: '0' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(color).getByRole('button', { name: /Zelena/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows the side fields only for a known number of corners, as in the mockup', async () => {
    renderChat(FENCE, recordingClient().client)
    const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
    expect(screen.queryByRole('group', { name: 'Dužina svake strane (m)' })).not.toBeInTheDocument()
    fireEvent.click(within(corners).getByRole('button', { name: '1' }))
    const sides = screen.getByRole('group', { name: 'Dužina svake strane (m)' })
    expect(within(sides).getAllByRole('textbox')).toHaveLength(2)
    expect(within(sides).queryByRole('button', { name: copy.chat.clarify.addSide })).not.toBeInTheDocument()
    fireEvent.click(within(corners).getByRole('button', { name: 'Ne znam' }))
    expect(screen.queryByRole('group', { name: 'Dužina svake strane (m)' })).not.toBeInTheDocument()
  })

  it('lets "Ne znam" corners be sent without side lengths', async () => {
    const { client, requests } = recordingClient()
    renderChat(FENCE, client)
    const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
    fireEvent.click(within(corners).getByRole('button', { name: 'Ne znam' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Gdje idu stupovi?' })).getByRole('button', { name: /pločicom/ }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Koja boja?' })).getByRole('button', { name: /Antracit/ }))
    fireEvent.click(screen.getByRole('button', { name: copy.chat.clarify.submit }))
    await expect.poll(() => requests.length).toBe(2)
    expect(requests[1].answers).toEqual({ corners: 'unknown', closed: 'no', posts: 'plate', color: 'antracit' })
  })

  it('raises the corners with "+ strana" from 4+ so corners and sides agree', async () => {
    renderChat(FENCE, recordingClient().client)
    const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
    fireEvent.click(within(corners).getByRole('button', { name: '4+' }))
    const sides = screen.getByRole('group', { name: 'Dužina svake strane (m)' })
    expect(within(sides).getAllByRole('textbox')).toHaveLength(5)
    fireEvent.click(within(sides).getByRole('button', { name: copy.chat.clarify.addSide }))
    expect(within(sides).getAllByRole('textbox')).toHaveLength(6)
    expect(screen.getByRole('complementary', { name: copy.chat.project.label })).toHaveTextContent('5 kutova')
  })

  it('shows the closed-loop question as the "Ide okolo (zatvoreno)" toggle under the corners', async () => {
    renderChat(FENCE, recordingClient().client)
    const corners = await screen.findByRole('group', { name: 'Koliko ima kutova?' })
    expect(screen.queryByRole('group', { name: 'Ide li ograda okolo (zatvoreno)?' })).not.toBeInTheDocument()
    const toggle = within(corners).getByRole('button', { name: copy.chat.clarify.closedToggle })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(within(corners).getByRole('button', { name: '4+' })).toHaveAttribute('aria-pressed', 'true')
    const sides = screen.getByRole('group', { name: 'Dužina svake strane (m)' })
    expect(within(sides).getAllByRole('textbox')).toHaveLength(4)
    const panel = screen.getByRole('complementary', { name: copy.chat.project.label })
    expect(panel).toHaveTextContent('4 kuta, zatvoreno')
    expect(panel).toHaveTextContent('? + ? + ? + ? m')
  })

  it('keeps "Prikaži prijedlog" disabled until every question is answered', async () => {
    renderChat(FENCE, recordingClient().client)
    const submit = await screen.findByRole('button', { name: copy.chat.clarify.submit })
    expect(submit).toBeDisabled()
    await answerClarify()
    expect(submit).toBeEnabled()
  })

  it('sends the answers with the thread id and shows product cards with a variant picker and OLX link', async () => {
    const { client, requests } = recordingClient()
    renderChat(FENCE, client)
    await answerClarify()
    fireEvent.click(screen.getByRole('button', { name: copy.chat.clarify.submit }))

    const card = (await screen.findByRole('heading', { name: 'Panelna ograda 3D (Nylofor)' })).closest('article')!
    expect(requests[1]).toEqual({
      message: '2 kuta · 10 + 5 + 10 m · Na beton, s pločicom · Antracit (RAL 7016)',
      threadId: '3b6f2a0e-7c1d-4e7a-9a43-2f1c5d8e9b10',
      answers: { corners: '2', closed: 'no', sides: '10 + 5 + 10', posts: 'plate', color: 'antracit' },
    })
    expect(screen.getByText(requests[1].message)).toBeInTheDocument()

    const variants = within(card).getByRole('group', { name: copy.chat.cards.variants })
    expect(within(variants).getByRole('button', { name: '153 × 250' })).toHaveAttribute('aria-pressed', 'true')
    expect(card).toHaveTextContent('53,00 KM')
    fireEvent.click(within(variants).getByRole('button', { name: '103 × 250' }))
    expect(card).toHaveTextContent('38,00 KM')
    fireEvent.click(within(variants).getByRole('button', { name: '153 × 200' }))
    expect(card).toHaveTextContent(copy.chat.cards.onRequest)

    const olx = within(card).getByRole('link', { name: copy.chat.cards.olxLabel('Panelna ograda 3D (Nylofor)') })
    expect(olx).toHaveAttribute(
      'href',
      'https://martom.olx.ba/artikal/panelna-ograda-83-103-123-i-153cm-fi-420440mm-200-i-250cm-duzi-63996799',
    )
    expect(olx).toHaveAttribute('target', '_blank')
    expect(olx).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('sends a typed message with the thread id', async () => {
    const { client, requests } = recordingClient()
    renderChat('Imate li dugi katanac?', client)
    await screen.findByRole('heading', { name: 'Katanac dugi 50/40 i 30 mm' })
    fireEvent.change(screen.getByLabelText(copy.chat.composer.label), { target: { value: 'A brusilica?' } })
    fireEvent.click(screen.getByRole('button', { name: copy.chat.composer.submit }))
    await expect.poll(() => requests.length).toBe(2)
    expect(requests[1]).toEqual({
      message: 'A brusilica?',
      threadId: '8d1e4c52-0b9f-4a6e-bf3c-5a7d2e9c1f04',
      answers: null,
    })
  })

  it('shows the rate-limited copy with the retry time', async () => {
    renderChat('x', recordingClient(() => fixtures.rateLimited).client)
    expect(await screen.findByRole('alert')).toHaveTextContent(copy.chat.errors.rate_limited(30))
  })

  it('shows the internal error copy and keeps the text that arrived', async () => {
    renderChat('x', recordingClient(() => fixtures.internalError).client)
    expect(await screen.findByRole('alert')).toHaveTextContent(copy.chat.errors.internal)
    expect(screen.getByText('Gledam koje')).toBeInTheDocument()
  })

  it('shows an unknown event type as an error, not silently', async () => {
    const stream = [{ type: 'token', text: 'Evo ' }, { type: 'mystery', payload: 1 }, { type: 'done', thread_id: 't' }]
    renderChat('x', recordingClient(() => stream).client)
    expect(await screen.findByRole('alert')).toHaveTextContent(copy.chat.errors.unknown_event)
  })

  it('shows a connection error when the stream breaks', async () => {
    const client: ChatClient = {
      // oxlint-disable-next-line require-yield -- a stream that fails before its first event
      async *stream() {
        throw new Error('network down')
      },
    }
    renderChat('x', client)
    expect(await screen.findByRole('alert')).toHaveTextContent(copy.chat.errors.connection)
  })

  it('handles every event type of every fixture without an error', async () => {
    const all = Object.values(fixtures)
    expect([...new Set(all.flat().map((event) => event.type))].sort()).toEqual([...CHAT_EVENT_TYPES].sort())
    // The error fixtures are checked for their own copy above.
    const withoutErrors = all.filter((events) => events.every((event) => event.type !== 'error'))
    expect(withoutErrors.length).toBeGreaterThan(0)
    for (const events of withoutErrors) {
      const { unmount } = renderChat('x', recordingClient(() => events).client)
      await expect.poll(() => screen.queryByText(copy.chat.writing)).toBeNull()
      await expect.poll(() => screen.queryByText(copy.chat.steps.live)).toBeNull()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      unmount()
    }
  })

  it('shows the materials list title and lines from the bom event', async () => {
    renderChat('x', recordingClient(() => fixtures.fenceConcretePosts).client)
    const bom = await screen.findByRole('region', { name: 'Popis materijala' })
    expect(bom).toHaveTextContent('25 m · panel 153 cm · antracit')
    expect(bom).toHaveTextContent('Panel 153 × 250 cm')
    expect(bom).toHaveTextContent('Stupovi za betoniranje – po upitu')
  })

  it('on phones, shows the materials list in the conversation and recalculates through the interface', async () => {
    renderChat('x', recordingClient(() => fixtures.fenceBom).client)
    const conversation = screen.getByRole('region', { name: copy.chat.label })
    const bom = await within(conversation).findByRole('region', { name: 'Popis materijala' })
    const total = within(bom).getByTestId('bom-total')
    expect(total).toHaveTextContent(copy.chat.bom.totalLabel.card)
    expect(total).toHaveTextContent('913,10 KM')
    fireEvent.click(within(bom).getByRole('button', { name: copy.chat.bom.more('Panel 153 × 250 cm') }))
    await expect.poll(() => total.textContent).toContain('966,10 KM')
    expect(within(bom).getByRole('listitem', { name: 'Panel 153 × 250 cm' })).toHaveTextContent('583,00')
  })

  describe('on wide screens', () => {
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    function stubWideScreen() {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query.includes('min-width'),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }))
    }

    it('shows the materials list as the panel beside the conversation', async () => {
      stubWideScreen()
      renderChat('x', recordingClient(() => fixtures.fenceBom).client)
      const bom = await screen.findByRole('region', { name: 'Popis materijala' })
      const conversation = screen.getByRole('region', { name: copy.chat.label })
      expect(conversation).not.toContainElement(bom)
      expect(within(bom).getByTestId('bom-total')).toHaveTextContent(copy.chat.bom.totalLabel.panel)
      expect(bom).toHaveTextContent(copy.chat.bom.panelFootnote)
    })
  })
})
