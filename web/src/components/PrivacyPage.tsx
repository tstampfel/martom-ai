import { useEffect } from 'react'
import logo from '../assets/martom-logo.png'
import logoWhite from '../assets/martom-logo-white.png'
import { copy, telHref } from '../copy'
import './AppShell.css'
import './PrivacyPage.css'

const phoneHref = telHref(copy.shop.phone)
const page = copy.privacyPage

/**
 * The "Privatnost" page (docs/mockups/{web,mobile}/6-privatnost.html). It has
 * its own header, with a link back to the advisor instead of the categories
 * and the cart; the top strip and the wide footer reuse AppShell's styles.
 */
export function PrivacyPage() {
  // The tab title follows the page; index.html keeps the site's own title.
  useEffect(() => {
    const previous = document.title
    document.title = page.documentTitle
    return () => {
      document.title = previous
    }
  }, [])

  return (
    <div className="shell">
      <div className="top-strip">
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
        <div className="site-header__inner privacy-page__header">
          <a className="site-header__home" href="/" aria-label={copy.header.homeLabel}>
            <img className="site-header__logo" src={logo} alt={copy.header.logoAlt} width={992} height={304} />
          </a>
          <a className="privacy-page__back privacy-page__back--wide" href="/">
            {page.back}
          </a>
          <a className="privacy-page__back privacy-page__back--narrow" href="/">
            {page.backShort}
          </a>
        </div>
      </header>

      <main className="shell__main privacy-page">
        <span className="privacy-page__eyebrow">{page.eyebrow}</span>
        <h1 className="privacy-page__title">{page.title}</h1>
        <p className="privacy-page__intro privacy-page__intro--wide">{page.intro}</p>
        <p className="privacy-page__intro privacy-page__intro--narrow">{page.introShort}</p>
        {page.sections.map((section) => (
          <section key={section.heading} className="privacy-page__section">
            <h2 className="privacy-page__heading">{section.heading}</h2>
            <p className="privacy-page__text">
              {section.lines.map((line, i) => (
                <span key={i}>
                  {i > 0 && <br />}
                  {'label' in line && <b>{line.label}</b>}
                  {line.text}
                </span>
              ))}
              {'contact' in section && (
                <>
                  <a className="privacy-page__link" href={`mailto:${copy.shop.email}`}>
                    {copy.shop.email}
                  </a>
                  {` · ${copy.shop.phone}.`}
                </>
              )}
            </p>
          </section>
        ))}
      </main>

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
        {/* On phones this footer shows the address, not the hours (mobile/6-privatnost.html). */}
        <div className="site-footer__inner site-footer__inner--narrow">
          <span>{copy.shop.address}</span>
          <a className="site-footer__phone" href={phoneHref}>
            {copy.shop.phone}
          </a>
        </div>
      </footer>
    </div>
  )
}
