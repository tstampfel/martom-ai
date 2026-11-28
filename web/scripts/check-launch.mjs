// Launch check for the customer page: fails while the production build still
// holds a "Privatnost" placeholder that has to be filled in before launch
// (`privacyPlaceholders` in src/copy.ts: the date Tomislav sets at launch and
// the order retention period Toni confirms).
//
//   npm --prefix web run build
//   npm --prefix web run check:launch      # scans web/dist
//   node web/scripts/check-launch.mjs DIR  # scans another build folder
//
// Exit 0: no placeholder found. Exit 1: a placeholder is still there (each
// one is printed with the file it is in), or the folder has no build.
// src/launchCheck.test.ts checks that this list catches every value of
// `privacyPlaceholders`.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** The start of each placeholder, as the canvas writes it. */
const PLACEHOLDERS = ['[DATUM]', '[ROK ČUVANJA']

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

const scriptPath = fileURLToPath(import.meta.url)
const distDir = resolve(process.argv[2] ?? join(scriptPath, '../../dist'))

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

const found = []
for (const file of files) {
  const text = readFileSync(file, 'utf8')
  for (const placeholder of PLACEHOLDERS) {
    const variants = [placeholder, escaped(placeholder), escaped(placeholder, { upperHex: true })]
    if (variants.some((variant) => text.includes(variant))) {
      found.push(`${placeholder} in ${relative(distDir, file)}`)
    }
  }
}

if (found.length > 0) {
  console.error('check:launch FAILED: the "Privatnost" page still has placeholders to fill in before launch:')
  for (const line of found) console.error(`  ${line}`)
  console.error('Fill them in on the canvas first, then in privacyPlaceholders (web/src/copy.ts).')
  process.exit(1)
}
console.log(`check:launch: no "Privatnost" placeholders in ${files.length} build files.`)
