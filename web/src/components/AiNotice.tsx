import './AiNotice.css'

/** A bold lead and the rest of the sentence. */
type Wording = { readonly lead: string; readonly rest: string }
type NoticeText = { readonly wide: Wording; readonly short: Wording }

type AiNoticeProps = {
  /** The web and phone wordings (copy.aiNotice.start or .chat). */
  text: NoticeText
  /** `on-brand`: white on the red hero; `muted`: grey under the chat input. */
  tone: 'on-brand' | 'muted'
  /** Lets the chat region point at the notice with aria-describedby. */
  id?: string
}

/**
 * "Odgovara AI savjetnik…" / "Razgovarate s AI savjetnikom…" (EU AI Act
 * Art. 50, #151). Both wordings are rendered and CSS shows one per
 * breakpoint, so screen readers read only the visible one. It is plain text,
 * never a live region: it is read once, in reading order or with the region
 * it describes.
 */
export function AiNotice({ text, tone, id }: AiNoticeProps) {
  return (
    <p id={id} className={`ai-notice ai-notice--${tone}`}>
      <span className="ai-notice__wide">
        <strong className="ai-notice__lead">{text.wide.lead}</strong>
        {text.wide.rest}
      </span>
      <span className="ai-notice__short">
        <strong className="ai-notice__lead">{text.short.lead}</strong>
        {text.short.rest}
      </span>
    </p>
  )
}
