import { AppShell } from './components/AppShell'
import { Hexagon } from './components/Hexagon'
import { copy } from './copy'
import './App.css'

// M5-1 ships the shell, the red hero heading and the badges. The question box,
// quick-start chips, "Savjetnik radi" card, category tiles and "Kako radi"
// arrive with the start screen (M5-2).
export default function App() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero__panel">
          {/* Both wordings are rendered; CSS shows one per breakpoint. */}
          <p className="hero__eyebrow">
            <span className="hero__wide">{copy.hero.eyebrow}</span>
            <span className="hero__short">{copy.hero.eyebrowShort}</span>
          </p>
          <h1 className="hero__title">
            <span className="hero__wide">
              {copy.hero.title[0]}
              <br />
              {copy.hero.title[1]}
            </span>
            <span className="hero__short">{copy.hero.titleShort}</span>
          </h1>
          <p className="hero__lead">{copy.hero.lead}</p>
        </div>
      </section>

      <section className="about" aria-label={copy.about.label}>
        <ul className="about__list">
          {copy.about.badges.map((badge) => (
            <li key={badge.value} className={'short' in badge ? 'about__item' : 'about__item about__item--wide-only'}>
              <Hexagon compact={badge.value.length > 2}>{badge.value}</Hexagon>
              {'short' in badge ? (
                <>
                  <span className="about__text--wide">{badge.text}</span>
                  <span className="about__text--short">{badge.short}</span>
                </>
              ) : (
                <span>{badge.text}</span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </AppShell>
  )
}
