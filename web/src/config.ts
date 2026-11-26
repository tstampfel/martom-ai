// Runtime settings for the customer page. Vite inlines `VITE_*` variables at
// build time (set them in web/.env.local or the Cloudflare Pages settings).
// Only public values belong here: everything in the bundle is readable by
// anyone. Never put ORDER_PHONE or any private number here.

/** Martom's public shop number, as printed on the shop's site and the mockups. */
const DEFAULT_SHOP_PHONE = '036 651 427'

type Env = { VITE_SHOP_PHONE?: string }

export type Config = {
  /** Number behind every "call Martom" button and link. */
  shopPhone: string
}

export function readConfig(env: Env): Config {
  return {
    shopPhone: env.VITE_SHOP_PHONE?.trim() || DEFAULT_SHOP_PHONE,
  }
}

export const config = readConfig({ VITE_SHOP_PHONE: import.meta.env.VITE_SHOP_PHONE })
