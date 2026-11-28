// Launch check for the customer page. It fails while
//  - the production build still holds a "Privatnost" placeholder that has to
//    be filled in before launch (`privacyPlaceholders` in src/copy.ts: the
//    date Tomislav sets at launch), or
//  - Martom's legal details for the footer of every page (src/companyDetails.json:
//    the full company name, address, JIB, and the PDV number if Martom is
//    VAT-registered) are not all filled in, or are missing from the build.
//    The values come only from Tomislav or Toni, through the canvas.
//
//   npm --prefix web run build
//   npm --prefix web run check:launch                         # scans web/dist
//   node web/scripts/check-launch.mjs DIR [--company FILE]    # another build or details file
//
// Exit 0: nothing left to fill in. Exit 1: something is (each problem is
// printed), or the folder has no build. src/launchCheck.test.ts checks that
// this catches every value of `privacyPlaceholders` and every missing detail.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** The start of each placeholder, as the canvas writes it. */
const PLACEHOLDERS = ['[DATUM]']

/** Build files that can carry page text. */
const TEXT_FILE = /\.(html|js|mjs|css|json|txt)$/

/** A string as a bundler may write it when it escapes non-ASCII ("Č" as a u-escape, 010C or 010c). */
function escaped(text, { upperHex = false } = {}) {
  return text.replace(/[^\x20-\x7e]/g, (char) => {
    const hex = char.charCodeAt(0).toString(16).padStart(4, '0')
    return `\\u${upperHex ? hex.toUpperCase() : hex}`
  })
}

function textFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return textFiles(path)
    return TEXT_FILE.test(name) ? [path] : []
  })
}

/**
 * The problems with Martom's legal details: an empty name or address, a JIB
 * that isn't 13 digits, VAT registration not stated (`vatRegistered` null), a
 * PDV number that isn't 12 digits when Martom is VAT-registered, or a PDV
 * number given when it isn't.
 */
function companyProblems(company) {
  const problems = []
  const text = (key) => (typeof company[key] === 'string' ? company[key].trim() : '')
  if (!text('legalName')) problems.push('legalName (the full company name) is empty')
  if (!text('address')) problems.push('address is empty')
  if (!/^\d{13}$/.test(text('jib'))) problems.push('jib must be the 13-digit JIB')
  if (company.vatRegistered === true) {
    if (!/^\d{12}$/.test(text('vatNumber'))) problems.push('vatNumber must be the 12-digit PDV number')
  } else if (company.vatRegistered === false) {
    if (text('vatNumber')) problems.push('vatNumber is set but vatRegistered is false')
  } else {
    problems.push('vatRegistered must be true or false (is Martom VAT-registered?)')
  }
  return problems
}

/** The values the footer must show, as they would appear in the build. */
function companyValues(company) {
  const values = [company.legalName, company.address, company.jib]
  if (company.vatRegistered === true) values.push(company.vatNumber)
  return values.map((value) => String(value).trim())
}

/** Whether `value` is in `text` as written or with \u escapes. */
function contains(text, value) {
  return [value, escaped(value), escaped(value, { upperHex: true })].some((variant) => text.includes(variant))
}

const scriptPath = fileURLToPath(import.meta.url)
const args = process.argv.slice(2)
const companyFlag = args.indexOf('--company')
const companyFile = resolve(
  companyFlag >= 0 ? args[companyFlag + 1] : join(scriptPath, '../../src/companyDetails.json'),
)
if (companyFlag >= 0) args.splice(companyFlag, 2)
const distDir = resolve(args[0] ?? join(scriptPath, '../../dist'))

let files
try {
  files = textFiles(distDir)
} catch {
  console.error(`check:launch: no build at ${distDir}; run \`npm --prefix web run build\` first.`)
  process.exit(1)
}
if (files.length === 0) {
  console.error(`check:launch: no build files in ${distDir}; run \`npm --prefix web run build\` first.`)
  process.exit(1)
}

const texts = files.map((file) => ({ file, text: readFileSync(file, 'utf8') }))

const found = []
for (const { file, text } of texts) {
  for (const placeholder of PLACEHOLDERS) {
    if (contains(text, placeholder)) found.push(`${placeholder} in ${relative(distDir, file)}`)
  }
}

let company = {}
const companyErrors = []
try {
  company = JSON.parse(readFileSync(companyFile, 'utf8'))
} catch {
  companyErrors.push(`can't read ${companyFile}`)
}
companyErrors.push(...companyProblems(company))
if (companyErrors.length === 0) {
  for (const value of companyValues(company)) {
    if (!texts.some(({ text }) => contains(text, value))) companyErrors.push(`"${value}" is not in the build`)
  }
}

if (found.length > 0) {
  console.error('check:launch FAILED: the "Privatnost" page still has placeholders to fill in before launch:')
  for (const line of found) console.error(`  ${line}`)
  console.error('Fill them in on the canvas first, then in privacyPlaceholders (web/src/copy.ts).')
}
if (companyErrors.length > 0) {
  console.error("check:launch FAILED: the footer doesn't show Martom's full legal details yet:")
  for (const line of companyErrors) console.error(`  ${line}`)
  console.error(
    'Tomislav or Toni give them through the canvas; then fill in web/src/companyDetails.json. Never guess them.',
  )
}
if (found.length > 0 || companyErrors.length > 0) process.exit(1)
console.log(`check:launch: no "Privatnost" placeholders and the company details are in ${files.length} build files.`)
