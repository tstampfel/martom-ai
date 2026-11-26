import type { ReactNode } from 'react'
import logo from '../assets/martom-logo.png'
import logoWhite from '../assets/martom-logo-white.png'
import { copy, telHref } from '../copy'
import { CartIcon, PhoneIcon } from './Icons'
import './AppShell.css'

const phoneHref = telHref(copy.shop.phone)

type AppShellProps = {
  children: ReactNode
  /** Starts a chat about a category picked in the header. */
  onStartChat: (text: string) => void
  cartCount?: number
}

/**
 * The frame around every screen: red top strip, header with logo and cart,
 * and footer. On phones (below 768 px) the strip collapses to a red border
 * and the shop hours and phone number move to the footer, as in
 * docs/mockups/mobile/.
 */
export function AppShell({ children, onStartChat, cartCount = 0 }: AppShellProps) {
  return (
    <div className="shell">
      <div className="top-strip" data-testid="top-strip">
        <div className="top-strip__inner">
          <span>{copy.topStrip.delivery}</span>
          <span>
            {copy.shop.hours} ·{' '}
            <a className="top-strip__phone" href={phoneHref}>
              {copy.shop.phone}
            </a>
          </span>
        </div>
      </div>

      <header className="site-header">
        <div className="site-header__inner">
          <a className="site-header__home" href="/" aria-label={copy.header.homeLabel}>
            <img className="site-header__logo" src={logo} alt={copy.header.logoAlt} width={992} height={304} />
          </a>
          <nav className="site-header__nav" aria-label={copy.header.navLabel}>
            {/* Each category starts a chat about it, like the quick-start chips. */}
            {copy.header.nav.map((item) => (
              <button key={item} type="button" className="site-header__category" onClick={() => onStartChat(item)}>
                {item}
              </button>
            ))}
          </nav>
          <div className="site-header__actions">
            <a className="icon-button icon-button--outline site-header__call" href={phoneHref} aria-label={copy.header.call}>
              <PhoneIcon />
            </a>
            {/* Placeholder target: the cart (korpa) screen arrives in M5-5. */}
            <a className="cart-button" href="#korpa" aria-label={copy.header.cart(cartCount)}>
              <CartIcon />
              <span className="cart-button__label" aria-hidden="true">
                {copy.header.cart(cartCount)}
              </span>
            </a>
          </div>
        </div>
      </header>

      <main className="shell__main">{children}</main>

      <footer className="site-footer">
        <div className="site-footer__inner site-footer__inner--wide">
          <span className="site-footer__brand">
            <img src={logoWhite} alt={copy.shop.name} width={992} height={308} />
            <span>{copy.shop.address}</span>
          </span>
          <span>
            <a href={`mailto:${copy.shop.email}`}>{copy.shop.email}</a> · <a href={phoneHref}>{copy.shop.phone}</a>
          </span>
        </div>
        <div className="site-footer__inner site-footer__inner--narrow">
          <span>{copy.shop.hours}</span>
          <a className="site-footer__phone" href={phoneHref}>
            {copy.shop.phone}
          </a>
        </div>
      </footer>
    </div>
  )
}
