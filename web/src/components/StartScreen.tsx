import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { copy } from '../copy'
import { usePrefersReducedMotion, useTypingPlaceholder } from '../hooks'
import { Hexagon } from './Hexagon'
import { ArrowIcon } from './Icons'
import './StartScreen.css'

type StartScreenProps = {
  /** Starts a chat with the customer's first message. */
  onStartChat: (text: string) => void
}

/**
 * The first screen: red hero with the question box and quick-start chips,
 * the hexagon badges and "Kako radi". Layout from docs/mockups/web/1-start.html
 * and docs/mockups/mobile/1-start.html; both are rendered and CSS picks one
 * where they differ.
 */
export function StartScreen({ onStartChat }: StartScreenProps) {
  const inputId = useId()
  const [question, setQuestion] = useState('')
  const reducedMotion = usePrefersReducedMotion()
  const typed = useTypingPlaceholder(copy.start.examples, reducedMotion || question !== '')
  // The trailing bar stands in for a text cursor, as in the mockups.
  const placeholder = reducedMotion ? copy.start.examples[0] : `${typed}|`

  function submit() {
    const text = question.trim()
    if (text) onStartChat(text)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  // Enter sends, Shift+Enter adds a new line (and Enter while an IME is
  // composing a character does nothing).
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

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

            <form className="ask" onSubmit={handleSubmit}>
              <label className="ask__label" htmlFor={inputId}>
                {copy.start.inputLabel}
              </label>
              <div className="ask__box">
                <textarea
                  id={inputId}
                  className="ask__input"
                  rows={2}
                  value={question}
                  placeholder={placeholder}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={handleKeyDown}
                />
                <button className="ask__submit" type="submit">
                  {copy.start.submit}
                  <ArrowIcon />
                </button>
              </div>
            </form>

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
            <AgentCard />
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

      {/* On phones the card sits below the badges instead of inside the hero. */}
      <div className="agent-card-narrow">
        <AgentCard />
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
 * Static picture of the advisor at work. It is an illustration, not live
 * data, so screen readers skip it.
 */
function AgentCard() {
  return (
    <div className="agent-card" aria-hidden="true">
      <div className="agent-card__head">
        <strong>{copy.agentCard.title}</strong>
        <span className="agent-card__badge">{copy.agentCard.badge}</span>
      </div>
      <span className="agent-card__quote">{copy.agentCard.quote}</span>
      {copy.agentCard.steps.map((step) => (
        <div key={step.text} className={`agent-card__step agent-card__step--${step.state}`}>
          <span className="agent-card__mark">{step.state === 'done' ? '✓' : '…'}</span>
          <span className="agent-card__text--wide">{step.text}</span>
          <span className="agent-card__text--short">{step.short}</span>
        </div>
      ))}
    </div>
  )
}
