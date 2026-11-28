import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { privacyPlaceholders } from './copy'

// web/scripts/check-launch.mjs: the launch check that fails while the build
// still holds a "Privatnost" placeholder.
const SCRIPT = join(import.meta.dirname, '../scripts/check-launch.mjs')

function check(dir: string) {
  const result = spawnSync(process.execPath, [SCRIPT, dir], { encoding: 'utf8' })
  return { status: result.status, output: result.stdout + result.stderr }
}

/** \u escapes for every non-ASCII character, as a bundler with an ASCII charset writes them. */
function asciiEscaped(text: string): string {
  return text.replace(/[^\x20-\x7e]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

describe('check:launch', () => {
  let dist: string

  beforeEach(() => {
    dist = mkdtempSync(join(tmpdir(), 'martom-launch-'))
    mkdirSync(join(dist, 'assets'))
    writeFileSync(join(dist, 'index.html'), '<!doctype html><title>Martom</title>')
  })

  afterEach(() => {
    rmSync(dist, { recursive: true, force: true })
  })

  it('passes a build without placeholders', () => {
    writeFileSync(join(dist, 'assets', 'index.js'), 'const t = "Ažurirano: 1. 12. 2026."')
    expect(check(dist)).toMatchObject({ status: 0 })
  })

  it.each(Object.entries(privacyPlaceholders))('fails while the %s placeholder is in the build', (_, value) => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const t = ${JSON.stringify(`Tekst ${value}.`)}`)
    const result = check(dist)
    expect(result.status).toBe(1)
    expect(result.output).toContain('assets/index.js')
  })

  it.each(Object.entries(privacyPlaceholders))('finds the %s placeholder written with \\u escapes', (_, value) => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const t = "${asciiEscaped(value)}"`)
    expect(check(dist).status).toBe(1)
  })

  it('fails when there is no build', () => {
    rmSync(dist, { recursive: true, force: true })
    const result = check(dist)
    expect(result.status).toBe(1)
    expect(result.output).toContain('no build')
  })
})
