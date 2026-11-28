import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { type CompanyDetails, companyDetails, companyLine } from './companyDetails'
import { privacyPlaceholders } from './copy'

// web/scripts/check-launch.mjs: the launch check that fails while the build
// still holds a "Privatnost" placeholder or the footer lacks Martom's legal
// details.
const SCRIPT = join(import.meta.dirname, '../scripts/check-launch.mjs')

// Obviously fake details, for the tests only: the real ones come from
// Tomislav or Toni through the canvas.
const FAKE_COMPANY: CompanyDetails = {
  legalName: 'Primjer Čelik d.o.o.',
  address: 'Testna 1, 88000 Testgrad',
  jib: '1234567890123',
  vatRegistered: true,
  vatNumber: '123456789012',
}

/** The page text of a build that shows `company` in the footer. */
function footerText(company: CompanyDetails): string {
  return JSON.stringify(companyLine(company) ?? '')
}

function check(dir: string, company: unknown = FAKE_COMPANY) {
  const file = join(dir, '..', `${basename(dir)}-company.json`)
  writeFileSync(file, JSON.stringify(company))
  const result = spawnSync(process.execPath, [SCRIPT, dir, '--company', file], { encoding: 'utf8' })
  rmSync(file, { force: true })
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

  it('passes a build without placeholders and with the company details', () => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const t = "Ažurirano: 1. 12. 2026."; const f = ${footerText(FAKE_COMPANY)}`)
    expect(check(dist)).toMatchObject({ status: 0 })
  })

  it('passes a company that is not VAT-registered without a PDV number', () => {
    const company = { ...FAKE_COMPANY, vatRegistered: false, vatNumber: '' }
    writeFileSync(join(dist, 'assets', 'index.js'), `const f = ${footerText(company)}`)
    expect(check(dist, company)).toMatchObject({ status: 0 })
  })

  // The repo's own file stays empty until Tomislav or Toni give the values.
  it('fails on the details as committed, until they are filled in', () => {
    writeFileSync(join(dist, 'assets', 'index.js'), 'const t = "Ažurirano: 1. 12. 2026."')
    const result = check(dist, companyDetails)
    expect(result.status).toBe(1)
    expect(result.output).toContain('legalName')
  })

  it.each([
    ['an empty company name', { legalName: '' }, 'legalName'],
    ['an empty address', { address: ' ' }, 'address'],
    ['a JIB that is not 13 digits', { jib: '123456789012' }, 'jib'],
    ['VAT registration not stated', { vatRegistered: null }, 'vatRegistered'],
    ['a missing PDV number', { vatNumber: '' }, 'vatNumber'],
    ['a PDV number while not VAT-registered', { vatRegistered: false }, 'vatNumber'],
  ])('fails on %s', (_, change, field) => {
    const company = { ...FAKE_COMPANY, ...change }
    writeFileSync(join(dist, 'assets', 'index.js'), `const f = ${footerText(FAKE_COMPANY)}`)
    const result = check(dist, company)
    expect(result.status).toBe(1)
    expect(result.output).toContain(field)
  })

  it('fails when a detail is not in the build', () => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const f = ${JSON.stringify(FAKE_COMPANY.legalName)}`)
    const result = check(dist)
    expect(result.status).toBe(1)
    expect(result.output).toContain(`"${FAKE_COMPANY.jib}" is not in the build`)
  })

  it('finds the company details written with \\u escapes', () => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const f = "${asciiEscaped(companyLine(FAKE_COMPANY) ?? '')}"`)
    expect(check(dist)).toMatchObject({ status: 0 })
  })

  it.each(Object.entries(privacyPlaceholders))('fails while the %s placeholder is in the build', (_, value) => {
    writeFileSync(join(dist, 'assets', 'index.js'), `const t = ${JSON.stringify(`Tekst ${value}.`)}; const f = ${footerText(FAKE_COMPANY)}`)
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
