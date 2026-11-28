import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'
import { config } from './config'
import { copy, telHref } from './copy'

describe('App shell', () => {
  it('shows the delivery note and opening hours in the red top strip', () => {
    render(<App />)
    const strip = screen.getByTestId('top-strip')
    expect(strip).toHaveTextContent('Dostava širom BiH · Lično preuzimanje u Međugorju')
    expect(strip).toHaveTextContent('Pon–pet 07–16 · sub 07–14')
  })

  it('links the logo home with a Bosnian accessible name', () => {
    render(<App />)
    const home = screen.getByRole('link', { name: 'Željezarija Martom, početna' })
    expect(within(home).getByRole('img', { name: 'Željezarija Martom' })).toBeInTheDocument()
  })

  it('has a cart button with the item count', () => {
    render(<App />)
    expect(screen.getByRole('link', { name: 'Korpa · 0' })).toBeInTheDocument()
  })

  it('lets the customer call the shop', () => {
    render(<App />)
    const callLinks = screen.getAllByRole('link', {
      name: (name) => name === config.shopPhone || name === copy.header.call,
    })
    expect(callLinks.length).toBeGreaterThan(0)
    for (const link of callLinks) {
      expect(link).toHaveAttribute('href', telHref(config.shopPhone))
    }
  })

  it('has banner, main and footer landmarks', () => {
    render(<App />)
    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Krstine 106, Međugorje')
  })

  it('shows the 90-day chat notice on the start screen only', () => {
    render(<App />)
    const footer = screen.getByRole('contentinfo')
    expect(footer).toHaveTextContent(copy.privacy.chatNotice)
    fireEvent.click(screen.getByRole('button', { name: 'Dvorište' }))
    expect(screen.getByRole('contentinfo')).not.toHaveTextContent(copy.privacy.chatNotice)
    expect(screen.getByRole('contentinfo')).not.toHaveTextContent(copy.privacy.chatNoticeShort)
  })

  it('has the web and the phone wording of the hero heading', () => {
    render(<App />)
    const heading = screen.getByRole('heading', { level: 1 })
    expect(within(heading).getByText(/Mi složimo cijeli popis\./)).toHaveClass('hero__wide')
    expect(within(heading).getByText('Recite šta ograđujete. Mi složimo popis.')).toHaveClass('hero__short')
  })

  it('has the web and the phone wording of the hero eyebrow', () => {
    render(<App />)
    expect(screen.getByText('AI savjetnik za ograde i alat')).toHaveClass('hero__wide')
    expect(screen.getByText('AI savjetnik')).toHaveClass('hero__short')
  })

  it('switches to the chat with the chosen chip as the first message', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Dvorište' }))
    expect(screen.queryByRole('heading', { level: 1, name: /Recite šta ograđujete/ })).not.toBeInTheDocument()
    const chat = screen.getByRole('region', { name: copy.chat.label })
    expect(within(chat).getByText('Dvorište')).toBeInTheDocument()
  })

  it('switches to the chat from a header category', () => {
    render(<App />)
    const nav = screen.getByRole('navigation', { name: copy.header.navLabel })
    fireEvent.click(within(nav).getByRole('button', { name: 'Alat' }))
    const chat = screen.getByRole('region', { name: copy.chat.label })
    expect(within(chat).getByText('Alat')).toBeInTheDocument()
  })

  it('goes back to the start screen from the chat', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Balkon' }))
    fireEvent.click(screen.getByRole('button', { name: copy.chat.back }))
    expect(screen.getByLabelText(copy.start.inputLabel)).toBeInTheDocument()
  })

  it('moves focus to the chat when it opens and to the question box when going back', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Balkon' }))
    expect(screen.getByRole('region', { name: copy.chat.label })).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: copy.chat.back }))
    expect(screen.getByLabelText(copy.start.inputLabel)).toHaveFocus()
  })

  it('shows the four hexagon badges about Martom', () => {
    render(<App />)
    const about = screen.getByRole('region', { name: 'O Martomu' })
    expect(within(about).getAllByRole('listitem')).toHaveLength(4)
  })
})

describe('Privatnost page (M6-5b)', () => {
  it('the start-screen footer links Privatnost to /privatnost', () => {
    render(<App path="/" />)
    const links = within(screen.getByRole('contentinfo')).getAllByRole('link', { name: copy.privacy.link })
    expect(links).toHaveLength(2)
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/privatnost')
    }
  })

  it.each(['/privatnost', '/privatnost/'])('%s shows the privacy page instead of the advisor', (path) => {
    render(<App path={path} />)
    expect(screen.getByRole('heading', { level: 1, name: copy.privacyPage.title })).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('any other path shows the advisor', () => {
    render(<App path="/nesto" />)
    expect(screen.queryByRole('heading', { level: 1, name: copy.privacyPage.title })).not.toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent(copy.privacy.chatNotice)
  })
})
