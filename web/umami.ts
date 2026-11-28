// Umami visit statistics (#156, #163; docs/analytics.md). The build puts the tracker
// script into index.html only when VITE_UMAMI_ENABLED is "true", which is set in the
// Cloudflare Pages *production* environment only: local dev, tests and preview deploys
// get no script at all.
//
// The tracker is cookieless. Tomislav (28 Nov 2026): no data-* option that adds an
// identifier (no distinct id, tag or salt). The options below only send less:
// - data-domains: count only the production hostname, so a preview deploy
//   (<branch>.martom-savjetnik.pages.dev) or a local copy never counts even with the flag on;
// - data-exclude-search / data-exclude-hash: never send the URL's query string or #hash.
//
// This file runs in Node at build time (vite.config.ts), not in the browser bundle.

import type { Plugin } from 'vite'

export type UmamiEnv = {
  VITE_UMAMI_ENABLED?: string
  VITE_UMAMI_HOST?: string
  VITE_UMAMI_WEBSITE_ID?: string
}

/** Where index.html wants the script; replaced (or removed) by the build. */
export const UMAMI_MARKER = '<!-- umami -->'

/** The only hostname whose visits count. */
export const PRODUCTION_HOSTNAME = 'martom-savjetnik.pages.dev'

// Umami's own address: https and a bare origin, e.g. https://d1234abcd.cloudfront.net.
// Umami runs at the root of its own CloudFront hostname (infra/umami.tf), never under a path.
const HOST_PATTERN = /^https:\/\/[a-z0-9.-]+$/i
// Umami website ids are UUIDs. Checking the format also keeps quotes out of the attribute.
const WEBSITE_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** The tracker's <script> tag, or '' when tracking is off. Throws on a half-done setup. */
export function umamiScriptTag(env: UmamiEnv): string {
  if (env.VITE_UMAMI_ENABLED !== 'true') return ''

  const host = env.VITE_UMAMI_HOST?.trim().replace(/\/$/, '') ?? ''
  if (!HOST_PATTERN.test(host)) {
    throw new Error(
      'VITE_UMAMI_ENABLED is "true", but VITE_UMAMI_HOST is not an https:// address without a path (the umami_url Terraform output).',
    )
  }
  const websiteId = env.VITE_UMAMI_WEBSITE_ID?.trim() ?? ''
  if (!WEBSITE_ID_PATTERN.test(websiteId)) {
    throw new Error(
      'VITE_UMAMI_ENABLED is "true", but VITE_UMAMI_WEBSITE_ID is not a website id (a UUID from the Umami admin UI).',
    )
  }

  return (
    `<script defer src="${host}/script.js" data-website-id="${websiteId}"` +
    ` data-domains="${PRODUCTION_HOSTNAME}" data-exclude-search="true"` +
    ' data-exclude-hash="true"></script>'
  )
}

/** index.html with the marker replaced by the script, or simply removed when tracking is off. */
export function injectUmami(html: string, env: UmamiEnv): string {
  return html.replace(UMAMI_MARKER, umamiScriptTag(env))
}

/** Vite plugin: applies injectUmami to index.html in dev and build. */
export function umamiPlugin(env: UmamiEnv): Plugin {
  return {
    name: 'martom-umami',
    transformIndexHtml: (html) => injectUmami(html, env),
  }
}
