import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { companyDetails, companyLine } from '../companyDetails'
import { config } from '../config'
import { copy, telHref } from '../copy'
import { AppShell, SiteFooter } from './AppShell'

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

  it('has no Privatnost link without a privacy page', () => {
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

describe('AppShell footer credit (M5-9)', () => {
  function creditLinks() {
    return within(screen.getByRole('contentinfo')).getAllByRole('link', { name: copy.footer.credit })
  }

  it('on the start screen, follows Privatnost in the wide notice and has its own line on phones', () => {
    render(
      <AppShell onStartChat={() => {}} showPrivacyNotice privacyHref="/privatnost">
        content
      </AppShell>,
    )
    const [inNotice, ownLine] = creditLinks()
    expect(creditLinks()).toHaveLength(2)
    // docs/mockups/web/1-start.html: "... · Privatnost · Izradio i održava Stampfel Consulting"
    const wide = inNotice.closest('p')
    expect(wide).toHaveClass('privacy-notice--wide')
    expect(wide?.textContent).toMatch(new RegExp(`${copy.privacy.link} · ${copy.footer.creditLead} ${copy.footer.credit}$`))
    // docs/mockups/mobile/1-start.html: its own line under the phone notice.
    expect(ownLine.closest('p')).toHaveClass('site-footer__credit')
  })

  // The chat screens' canvas footers have no credit (web 2-5, mobile 2-clarify).
  it('is not shown on the chat screens', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    expect(
      within(screen.getByRole('contentinfo')).queryByRole('link', { name: copy.footer.credit }),
    ).not.toBeInTheDocument()
  })

  it('opens the credit link in a new tab', () => {
    render(
      <AppShell onStartChat={() => {}} showPrivacyNotice>
        content
      </AppShell>,
    )
    expect(creditLinks()[0]).toHaveAttribute('href', 'https://stampfelconsulting.com/')
    expect(creditLinks()[0]).toHaveAttribute('target', '_blank')
    expect(creditLinks()[0]).toHaveAttribute('rel', 'noopener')
  })
})

describe('SiteFooter company details (#174)', () => {
  it('shows nothing while the details are not filled in', () => {
    expect(companyLine(companyDetails)).toBeNull()
    const { container } = render(<SiteFooter />)
    expect(container.querySelector('.site-footer__company')).toBeNull()
  })

  it('shows the full name, address, JIB and PDV number once they are', () => {
    // Obviously fake values: the real ones come from Tomislav or Toni.
    const line = companyLine({
      legalName: 'Primjer d.o.o.',
      address: 'Testna 1, Testgrad',
      jib: '1234567890123',
      vatRegistered: true,
      vatNumber: '123456789012',
    })
    expect(line).toBe('Primjer d.o.o. · Testna 1, Testgrad · JIB: 1234567890123 · PDV broj: 123456789012')
    render(<SiteFooter company={line} />)
    expect(within(screen.getByRole('contentinfo')).getByText(line ?? '')).toHaveClass('site-footer__company')
  })

  it('leaves out the PDV number when Martom is not VAT-registered', () => {
    const line = companyLine({
      legalName: 'Primjer d.o.o.',
      address: 'Testna 1, Testgrad',
      jib: '1234567890123',
      vatRegistered: false,
      vatNumber: '',
    })
    expect(line).toBe('Primjer d.o.o. · Testna 1, Testgrad · JIB: 1234567890123')
  })
})
