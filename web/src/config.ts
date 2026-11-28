// Runtime settings for the customer page. Vite inlines `VITE_*` variables at
// build time (set them in web/.env.local or the Cloudflare Pages settings).
// Only public values belong here: everything in the bundle is readable by
// anyone. Never put ORDER_PHONE or any private number here.

/** Martom's public shop number, as printed on the shop's site and the mockups. */
const DEFAULT_SHOP_PHONE = '036 651 427'

type Env = { VITE_SHOP_PHONE?: string; VITE_LANGSMITH_ENABLED?: string }

export type Config = {
  /** Number behind every "call Martom" button and link. */
  shopPhone: string
  /**
   * Whether the API sends traces to LangSmith in this deployment. The
   * "Privatnost" page names LangSmith only when it is on, so set
   * VITE_LANGSMITH_ENABLED=true in the same release that turns tracing on
   * (#150). Only the exact value "true" turns it on.
   */
  langSmithEnabled: boolean
}

export function readConfig(env: Env): Config {
  return {
    shopPhone: env.VITE_SHOP_PHONE?.trim() || DEFAULT_SHOP_PHONE,
    langSmithEnabled: env.VITE_LANGSMITH_ENABLED?.trim().toLowerCase() === 'true',
  }
}

export const config = readConfig({
  VITE_SHOP_PHONE: import.meta.env.VITE_SHOP_PHONE,
  VITE_LANGSMITH_ENABLED: import.meta.env.VITE_LANGSMITH_ENABLED,
})
