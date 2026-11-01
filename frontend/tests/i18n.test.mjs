// Interface language.
//
//     node tests/i18n.test.mjs        (from frontend/)
//     node tests/i18n.test.mjs --keys (print every key the source uses)
//
// The source text (Traditional Chinese) is the key, so the risk is drift:
// a string reworded in a component no longer matches its row in the tables
// and quietly shows in Chinese again; a string added without a row does the
// same. This reads every literal t('…') under src/ and compares.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { t, i18n, detectLocale, setLocale, LOCALES } from '../src/i18n.js'
import zhCN from '../src/locales/zh-CN.js'
import en from '../src/locales/en.js'

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

// ── what the source asks for ────────────────────────────────────────────────
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
function walk(dir) {
  return readdirSync(dir).flatMap(n => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return n === 'locales' ? [] : walk(p)
    return /\.(vue|js)$/.test(n) && n !== 'i18n.js' ? [p] : []
  })
}
const unescape = s => s.replace(/\\(.)/g, (_, c) => ({ n: '\n', t: '\t' })[c] ?? c)
const used = new Map()          // key → first file using it
const dynamic = []
for (const f of walk(SRC)) {
  const text = readFileSync(f, 'utf8')
  // t('…') where it is shown; N_('…') in a constant table shown through t()
  for (const m of text.matchAll(/(?<![\w.$])(?:t|N_)\(\s*'((?:[^'\\\n]|\\.)*)'/g)) {
    const k = unescape(m[1])
    if (!used.has(k)) used.set(k, f.slice(SRC.length + 1))
  }
  // a template literal or variable cannot be checked, and a ${…} inside the
  // key means every value is a different key — use {name} params instead
  for (const m of text.matchAll(/(?<![\w.$])(?:t|N_)\(\s*`/g)) dynamic.push(f.slice(SRC.length + 1))
}

if (process.argv.includes('--keys')) {
  for (const k of used.keys()) console.log(JSON.stringify(k))
  process.exit(0)
}

console.log(`source uses ${used.size} keys`)
check('no t(`template literal`) keys', dynamic, [])
for (const [code, table] of [['zh-CN', zhCN], ['en', en]]) {
  const missing = [...used.keys()].filter(k => !(k in table))
  const orphan = Object.keys(table).filter(k => !used.has(k))
  check(`${code}: every key has a translation`,
        missing.map(k => `${k}  (${used.get(k)})`), [])
  check(`${code}: no rows nothing uses`, orphan, [])
  // an object literal keeps the last of two equal keys without a word, so a
  // row added twice quietly loses one of its translations
  const rows = [...readFileSync(join(SRC, 'locales', `${code}.js`), 'utf8')
    .matchAll(/^\s+'((?:[^'\\\n]|\\.)*)':/gm)].map(m => m[1])
  check(`${code}: no key written twice`,
        rows.filter((k, i) => rows.indexOf(k) !== i), [])
  // a translation must keep the same {params}, or a value silently vanishes
  const bad = Object.entries(table).filter(([k, v]) => typeof v === 'string' &&
    JSON.stringify([...k.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort()) !==
    JSON.stringify([...v.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort()))
  check(`${code}: same {params} as the key`, bad.map(([k]) => k), [])
}

// ── t() itself ──────────────────────────────────────────────────────────────
console.log('\nt()')
setLocale('zh-TW')
check('zh-TW is the key itself', t('全部展開'), '全部展開')
check('zh-TW fills params', t('{n} 個角色', { n: 3 }), '3 個角色')
setLocale('zh-CN')
check('zh-CN', t('全部展開'), '全部展开')
setLocale('en')
check('en', t('全部展開'), 'Expand all')
check('en plural, one', t('{n} 個角色', { n: 1 }), '1 character')
check('en plural, many', t('{n} 個角色', { n: 729 }), '729 characters')
check('untranslated falls back to the Chinese', t('沒有這一條 {x}', { x: 1 }), '沒有這一條 1')
check('a missing param is left visible, not blank', t('沒有這一條 {x}'), '沒有這一條 {x}')
setLocale('fr')
check('an unknown language is refused', i18n.locale, 'en')
setLocale('zh-TW')

console.log('\nbrowser language')
check('zh-TW', detectLocale(['zh-TW', 'en']), 'zh-TW')
check('zh-HK is Traditional', detectLocale(['zh-HK']), 'zh-TW')
check('zh-Hant', detectLocale(['zh-Hant-TW']), 'zh-TW')
check('zh-CN', detectLocale(['zh-CN']), 'zh-CN')
check('zh-SG is Simplified', detectLocale(['zh-SG']), 'zh-CN')
check('zh-Hans', detectLocale(['zh-Hans']), 'zh-CN')
check('en-US', detectLocale(['en-US']), 'en')
check('first match wins', detectLocale(['ja', 'en-GB', 'zh-TW']), 'en')
check('nothing we have', detectLocale(['ja', 'ko']), 'en')
check('no list', detectLocale(undefined), 'en')
check('three languages offered', LOCALES.map(l => l.code), ['zh-TW', 'zh-CN', 'en'])

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
