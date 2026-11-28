// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Read the file from disk: Vitest does not process CSS imports.
const tokensCss = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')

// The values in docs/mockups/README.md ("Design tokens"). If the design
// changes, change the README, this table and tokens.css together.
const expected: Record<string, string> = {
  '--color-red': '#D71222',
  '--color-charcoal': '#26292E',
  '--color-ink': '#16181D',
  '--color-muted': '#4F5560',
  '--color-muted-strong': '#3B4049',
  '--color-page': '#F2F2F0',
  '--color-card': '#FFFFFF',
  '--color-border': '#E2E2DE',
  '--color-border-strong': '#D5D6D1',
  '--color-on-dark': '#C9CDD4',
  '--color-on-dark-muted': '#A9AEB6',
  '--color-on-brand': '#FFFFFF',
  '--color-success': '#157A3A',
  '--color-success-tint': '#DDF2E3',
  '--color-dark-border': '#4A4F57',
  '--color-dark-border-soft': '#5A5F68',
  '--color-step-done': '#34C46A',
  '--color-step-working': '#F5A623',
  '--color-step-question': '#C27A00',
  '--color-photo-stripe': '#DEDED9',
  '--color-photo-stripe-light': '#E8E8E4',
  '--color-red-on-dark': '#FF4D5A',
  '--color-link': '#185FA5',
  '--color-link-hover': '#0C447C',
  '--hexagon': 'polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%)',
}

function readToken(css: string, name: string): string | undefined {
  const match = css.match(new RegExp(`${name}:\\s*([^;]+);`))
  return match?.[1].trim()
}

describe('design tokens', () => {
  it.each(Object.entries(expected))('%s is %s', (name, value) => {
    expect(readToken(tokensCss, name)?.toUpperCase()).toBe(value.toUpperCase())
  })

  it('uses Outfit for headings and Inter for body text', () => {
    expect(readToken(tokensCss, '--font-heading')).toMatch(/^'Outfit'/)
    expect(readToken(tokensCss, '--font-body')).toMatch(/^'Inter'/)
  })

  // One brand red (Tomislav, 26 Nov 2026). index.html and public/ can't use
  // CSS variables, so they repeat the hex; any reddish hex there must be it.
  it.each(['../../index.html', '../../public/favicon.svg'])(
    '%s uses no red other than --color-red',
    (path) => {
      const text = readFileSync(new URL(path, import.meta.url), 'utf8')
      const reds = (text.match(/#[0-9a-f]{6}\b/gi) ?? []).filter((hex) => {
        const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
        return r > 150 && g < 110 && b < 110
      })
      for (const red of reds) {
        expect(red.toUpperCase()).toBe(expected['--color-red'])
      }
    },
  )

  // WCAG 2.x contrast ratio of two #RRGGBB colours.
  function contrast(a: string, b: string): number {
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }

  // The footer credit (M5-9): text and link on the charcoal web footer, and
  // the muted text and link on the light phone footer.
  it.each([
    ['--color-on-dark', '--color-charcoal'],
    ['--color-on-dark-muted', '--color-charcoal'],
    ['--color-muted', '--color-page'],
  ])('%s on %s has a contrast of at least 4.5:1', (text, background) => {
    expect(contrast(expected[text], expected[background])).toBeGreaterThanOrEqual(4.5)
  })

  // The slogan's accent word (web start, 44 px Outfit 800) is large text, so
  // WCAG asks 3:1; it is 4.49:1. Never use it below 24 px (18.66 px bold).
  it('--color-red-on-dark on --color-charcoal reaches the large-text 3:1', () => {
    expect(contrast(expected['--color-red-on-dark'], expected['--color-charcoal'])).toBeGreaterThanOrEqual(3)
  })

  // 4.49:1 passes only for large text, so the token may only colour the
  // slogan's accent word (44 px Outfit 800). A new use needs large text and a
  // new entry here.
  it('uses --color-red-on-dark only for the slogan accent', () => {
    const srcDir = join(import.meta.dirname, '..')
    const uses: string[] = []
    for (const file of readdirSync(srcDir, { recursive: true }) as string[]) {
      if (!/\.(css|tsx?)$/.test(file) || file.endsWith('tokens.css') || file.endsWith('.test.ts')) continue
      // Comments may name the token; only real uses count.
      const text = readFileSync(join(srcDir, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
      for (const match of text.matchAll(/([^{}]*)\{[^}]*var\(--color-red-on-dark\)/g)) {
        uses.push(`${file}: ${match[1].trim()}`)
      }
      if (/\.tsx?$/.test(file) && text.includes('var(--color-red-on-dark)')) uses.push(`${file}: inline style`)
    }
    expect(uses).toEqual(['components/StartScreen.css: .slogan__accent'])
  })

  it('keeps touch targets at least 44 px', () => {
    expect(readToken(tokensCss, '--touch-target')).toBe('44px')
  })
})
