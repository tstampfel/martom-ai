// @vitest-environment node
import { readFileSync } from 'node:fs'
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
  '--color-on-brand': '#FFFFFF',
  '--color-success': '#157A3A',
  '--color-success-tint': '#DDF2E3',
  '--color-dark-border': '#4A4F57',
  '--color-dark-border-soft': '#5A5F68',
  '--color-step-done': '#34C46A',
  '--color-step-working': '#F5A623',
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

  it('keeps touch targets at least 44 px', () => {
    expect(readToken(tokensCss, '--touch-target')).toBe('44px')
  })
})
