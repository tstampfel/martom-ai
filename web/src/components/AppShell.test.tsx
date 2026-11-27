import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { config } from '../config'
import { copy, telHref } from '../copy'
import { AppShell } from './AppShell'

describe('AppShell header categories', () => {
  it.each(copy.header.nav)('"%s" starts a chat with that category', (category) => {
    const onStartChat = vi.fn<(text: string) => void>()
    render(<AppShell onStartChat={onStartChat}>content</AppShell>)
    const nav = screen.getByRole('navigation', { name: copy.header.navLabel })
    fireEvent.click(within(nav).getByRole('button', { name: category }))
    expect(onStartChat).toHaveBeenCalledExactlyOnceWith(category)
  })

  it('no longer links the categories to the OLX shop', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    const nav = screen.getByRole('navigation', { name: copy.header.navLabel })
    expect(within(nav).queryAllByRole('link')).toHaveLength(0)
  })
})

describe('AppShell call button', () => {
  it('calls the shop number from config', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    const call = screen.getByRole('link', { name: copy.header.call })
    expect(call).toHaveAttribute('href', telHref(config.shopPhone))
  })
})

describe('AppShell privacy notice (M5-8)', () => {
  it('shows the web and the phone wording from copy.ts when asked', () => {
    render(
      <AppShell onStartChat={() => {}} showPrivacyNotice>
        content
      </AppShell>,
    )
    const footer = screen.getByRole('contentinfo')
    expect(within(footer).getByText(copy.privacy.chatNotice, { exact: false })).toHaveClass('privacy-notice--wide')
    expect(within(footer).getByText(copy.privacy.chatNoticeShort, { exact: false })).toHaveClass(
      'privacy-notice--narrow',
    )
  })

  it('is hidden unless asked', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    expect(screen.queryByText(copy.privacy.chatNotice, { exact: false })).not.toBeInTheDocument()
    expect(screen.queryByText(copy.privacy.chatNoticeShort, { exact: false })).not.toBeInTheDocument()
  })

  it('has no Privatnost link until a privacy page exists (#129)', () => {
    render(
      <AppShell onStartChat={() => {}} showPrivacyNotice>
        content
      </AppShell>,
    )
    expect(screen.queryByRole('link', { name: copy.privacy.link })).not.toBeInTheDocument()
  })

  it('links Privatnost to the privacy page once one is given', () => {
    render(
      <AppShell onStartChat={() => {}} showPrivacyNotice privacyHref="/privatnost">
        content
      </AppShell>,
    )
    const links = screen.getAllByRole('link', { name: copy.privacy.link })
    expect(links).toHaveLength(2)
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/privatnost')
    }
  })
})
