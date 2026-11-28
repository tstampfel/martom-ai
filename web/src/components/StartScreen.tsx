import { agentStepStates } from '../agentSteps'
import logoWhite from '../assets/martom-logo-white.png'
import { copy } from '../copy'
import { useAgentCardSteps } from '../hooks'
import { Hexagon } from './Hexagon'
import { QuestionForm } from './QuestionForm'
import './StartScreen.css'

type StartScreenProps = {
  /** Starts a chat with the customer's first message. */
  onStartChat: (text: string) => void
  /** Focus the question box, e.g. when the customer comes back from the chat. */
  focusQuestion?: boolean
}

/**
 * The first screen: red hero with the question box and quick-start chips,
 * the hexagon badges and "Kako radi". Layout from docs/mockups/web/1-start.html
 * and docs/mockups/mobile/1-start.html; both are rendered and CSS picks one
 * where they differ.
 */
export function StartScreen({ onStartChat, focusQuestion = false }: StartScreenProps) {
  // One loop drives both copies of the card (hero on wide screens, below the
  // badges on phones), so they never drift apart.
  const shownSteps = useAgentCardSteps(copy.agentCard.steps)

  return (
    <>
      <section className="hero">
        <div className="hero__frame">
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

            <QuestionForm onSubmit={onStartChat} focusOnMount={focusQuestion} />

            <fieldset className="chips">
              <legend className="chips__legend">{copy.start.chipsLabel}</legend>
              {copy.start.chips.map((chip) => (
                <button
                  key={chip.label}
                  type="button"
                  className={'wideOnly' in chip ? 'chip chip--wide-only' : 'chip'}
                  // With a phone label both spans are in the DOM, so name the button explicitly.
                  aria-label={'short' in chip ? chip.label : undefined}
                  onClick={() => onStartChat(chip.message)}
                >
                  {'short' in chip ? (
                    <>
                      <span className="hero__wide">{chip.label}</span>
                      <span className="hero__short">{chip.short}</span>
                    </>
                  ) : (
                    chip.label
                  )}
                </button>
              ))}
            </fieldset>
          </div>

          <div className="hero__aside">
            <AgentCard shownSteps={shownSteps} />
          </div>
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

      {/* Wide screens only: phones hide it (StartScreen.css), the mobile canvas has none. */}
      <section className="slogan" aria-label={copy.brand.sloganLabel}>
        <div className="slogan__card">
          <p className="slogan__text">
            {copy.brand.slogan.before}
            <span className="slogan__accent">{copy.brand.slogan.accent}</span>
            <br />
            {copy.brand.slogan.after}
          </p>
          <img className="slogan__logo" src={logoWhite} alt={copy.shop.name} width={992} height={308} />
        </div>
      </section>

      {/* On phones the card sits below the badges instead of inside the hero. */}
      <div className="agent-card-narrow">
        <AgentCard shownSteps={shownSteps} />
      </div>

      <section className="how" aria-label={copy.howItWorks.label}>
        <ol className="how__list">
          {copy.howItWorks.steps.map((step, index) => (
            <li key={step.title} className="how__step">
              <span className="how__number" aria-hidden="true">
                {String(index + 1).padStart(2, '0')}
              </span>
              <strong className="how__title">{step.title}</strong>
              <span className="how__text">{step.text}</span>
            </li>
          ))}
        </ol>
      </section>
    </>
  )
}

/**
 * Example of the advisor at work, badged "primjer" because it is not live.
 * Its steps appear one by one (`shownSteps` of them are visible); hidden
 * steps keep their space so the hero doesn't jump. The animated copy is
 * hidden from screen readers, which get the full static list instead.
 */
function AgentCard({ shownSteps }: { shownSteps: number }) {
  const { title, badge, quote, steps } = copy.agentCard
  const states = agentStepStates(shownSteps, steps.length)

  return (
    <div className="agent-card">
      <div className="visually-hidden">
        <p>{quote}</p>
        <ul aria-label={`${title}, ${badge}`}>
          {steps.map((step) => (
            <li key={step.text}>{step.text}</li>
          ))}
        </ul>
      </div>
      <div className="agent-card__head" aria-hidden="true">
        <strong>{title}</strong>
        <span className="agent-card__badge">{badge}</span>
      </div>
      <span className="agent-card__quote" aria-hidden="true">
        {quote}
      </span>
      {steps.map((step, index) => {
        const state = states[index]
        return (
          <div
            key={step.text}
            className={`agent-card__step agent-card__step--${state}`}
            data-state={state}
            aria-hidden="true"
          >
            <span className="agent-card__mark">{state === 'done' ? '✓' : '…'}</span>
            <span>{step.text}</span>
          </div>
        )
      })}
    </div>
  )
}
