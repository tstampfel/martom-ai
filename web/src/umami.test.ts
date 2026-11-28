import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { UMAMI_MARKER, injectUmami, umamiScriptTag } from '../umami'

const WEBSITE_ID = '0b5e6f2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b'
const HOST = 'https://d1234abcd.cloudfront.net'
const ON = {
  VITE_UMAMI_ENABLED: 'true',
  VITE_UMAMI_HOST: HOST,
  VITE_UMAMI_WEBSITE_ID: WEBSITE_ID,
}

describe('index.html', () => {
  const html = readFileSync(join(import.meta.dirname, '../index.html'), 'utf8')

  it('has exactly one place for the Umami script, in <head>', () => {
    expect(html.split(UMAMI_MARKER)).toHaveLength(2)
    expect(html.indexOf(UMAMI_MARKER)).toBeLessThan(html.indexOf('</head>'))
  })

  it('carries no tracking script of its own (only the build adds it)', () => {
    expect(html).not.toContain('script.js')
  })
})

describe('umamiScriptTag', () => {
  it('is off when nothing is set (local dev, tests, previews)', () => {
    expect(umamiScriptTag({})).toBe('')
  })

  it('is off unless the flag is exactly "true"', () => {
    for (const flag of ['false', '0', '', 'TRUE ', 'yes']) {
      expect(umamiScriptTag({ ...ON, VITE_UMAMI_ENABLED: flag })).toBe('')
    }
  })

  it('loads the tracker from the Umami host, deferred, for the production site only', () => {
    const tag = umamiScriptTag(ON)
    expect(tag).toBe(
      `<script defer src="${HOST}/script.js" data-website-id="${WEBSITE_ID}"` +
        ' data-domains="martom-savjetnik.pages.dev" data-exclude-search="true"' +
        ' data-exclude-hash="true"></script>',
    )
  })

  it('accepts a host with a trailing slash', () => {
    expect(umamiScriptTag({ ...ON, VITE_UMAMI_HOST: `${HOST}/` })).toContain(
      `src="${HOST}/script.js"`,
    )
  })

  it('never adds options that identify a visitor (Tomislav: cookieless, no identifiers)', () => {
    const tag = umamiScriptTag(ON)
    for (const option of ['data-distinct-id', 'data-tag', 'data-salt', 'data-fetch-credentials']) {
      expect(tag).not.toContain(option)
    }
  })

  it('fails the build when the flag is on but the host or website id is missing or wrong', () => {
    expect(() => umamiScriptTag({ ...ON, VITE_UMAMI_HOST: undefined })).toThrow(/VITE_UMAMI_HOST/)
    expect(() => umamiScriptTag({ ...ON, VITE_UMAMI_HOST: 'http://d1234abcd.cloudfront.net' })).toThrow(
      /VITE_UMAMI_HOST/,
    )
    expect(() => umamiScriptTag({ ...ON, VITE_UMAMI_HOST: `${HOST}/umami` })).toThrow(/VITE_UMAMI_HOST/)
    expect(() => umamiScriptTag({ ...ON, VITE_UMAMI_WEBSITE_ID: '' })).toThrow(/VITE_UMAMI_WEBSITE_ID/)
    expect(() => umamiScriptTag({ ...ON, VITE_UMAMI_WEBSITE_ID: '" onload="x' })).toThrow(
      /VITE_UMAMI_WEBSITE_ID/,
    )
  })
})

describe('injectUmami', () => {
  const page = `<head>\n    ${UMAMI_MARKER}\n  </head>`

  it('replaces the marker with the script when the flag is on', () => {
    const out = injectUmami(page, ON)
    expect(out).toContain(`<script defer src="${HOST}/script.js"`)
    expect(out).not.toContain(UMAMI_MARKER)
  })

  it('removes the marker and adds nothing when the flag is off', () => {
    const out = injectUmami(page, {})
    expect(out).not.toContain('<script')
    expect(out).not.toContain(UMAMI_MARKER)
  })
})
