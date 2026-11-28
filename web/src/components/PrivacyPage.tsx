import { useEffect } from 'react'
import logo from '../assets/martom-logo.png'
import { config } from '../config'
import { copy, telHref } from '../copy'
import { SiteFooter, TopStrip } from './AppShell'
import './AppShell.css'
import './PrivacyPage.css'

const page = copy.privacyPage

/**
 * The "Privatnost" page (docs/mockups/{web,mobile}/6-privatnost.html). It has
 * its own header, with a link back to the advisor instead of the categories
 * and the cart; the top strip and the footer are AppShell's.
 *
 * `langSmith` says whether to name LangSmith among the processors and in
 * the transfer section; it follows VITE_LANGSMITH_ENABLED and is a prop
 * only so tests can show both.
 */
export function PrivacyPage({ langSmith = config.langSmithEnabled }: { langSmith?: boolean }) {
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
      <TopStrip />

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
              {section.lines
                .filter((line) => langSmith || !('langSmith' in line))
                .map((line, i) => (
                  <span key={i}>
                    {i > 0 && <br />}
                    {'lead' in line && line.lead}
                    {'label' in line && <b>{line.label}</b>}
                    {line.text}
                    {langSmith && 'langSmithText' in line && line.langSmithText}
                    {'link' in line && (
                      <a className="privacy-page__link" href={line.link.href} target="_blank" rel="noopener">
                        {line.link.label}
                      </a>
                    )}
                    {'textAfter' in line && line.textAfter}
                  </span>
                ))}
              {'contact' in section && (
                <>
                  <a className="privacy-page__link" href={`mailto:${copy.shop.email}`}>
                    {copy.shop.email}
                  </a>
                  {' · '}
                  <a className="privacy-page__link" href={telHref(copy.shop.phone)}>
                    {copy.shop.phone}
                  </a>
                  {section.contactAfter}
                </>
              )}
            </p>
          </section>
        ))}
      </main>

      {/* On phones this footer shows the address, not the hours (mobile/6-privatnost.html). */}
      <SiteFooter narrowStart={copy.shop.address} credit />
    </div>
  )
}
