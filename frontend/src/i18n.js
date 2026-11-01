// Interface language.
//
//   t('全部展開')                     → 全部展开 / Expand all
//   t('{n} 個角色', { n: 729 })        → 729 个角色 / 729 characters
//
// The Traditional Chinese text is the key (the user's decision, 2026-10-31):
// the code stays readable, zh-TW needs no table, and anything not yet
// translated shows in Chinese rather than as an opaque id. The tables in
// ./locales/ map that text to the other languages; a value may be a function
// of the params where the grammar needs it (English plurals).
// tests/i18n.test.mjs checks every literal t('…') in src/ has an entry in
// every table, and that no table keeps entries nothing uses any more.
//
// Reactive: t() reads `i18n.locale`, so a template or computed that calls it
// re-renders when the language changes. A string worked out once in setup
// (a const array of labels) does not — call t() where it is rendered.
//
// What is never translated: the user's own data (names, folders, tags,
// titles, group names they typed) and the verbatim text of OS or ffmpeg
// errors.
import { reactive } from 'vue'
import zhCN from './locales/zh-CN.js'
import en from './locales/en.js'

export const LOCALES = [
  { code: 'zh-TW', name: '繁體中文' },
  { code: 'zh-CN', name: '简体中文' },
  { code: 'en', name: 'English' },
]
const TABLES = { 'zh-CN': zhCN, en }
const KNOWN = new Set(LOCALES.map(l => l.code))
// the sign-in page has no account to ask, so it uses the last language this
// browser picked
const STORE_KEY = 'vam.lang'

export const i18n = reactive({ locale: 'zh-TW' })

function fill(s, params) {
  if (!params) return s
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m))
}

export function t(text, params) {
  const table = TABLES[i18n.locale]
  const v = table ? table[text] : undefined
  if (typeof v === 'function') return v(params || {})
  return fill(v ?? text, params)
}

/** Does the current language have a row for this exact text? */
export function has(text) {
  const table = TABLES[i18n.locale]
  return !!table && Object.prototype.hasOwnProperty.call(table, text)
}

/** Marks a key in a constant table without translating it yet:
 *    const FILTERS = [['all', N_('全部')], …]   →   {{ t(f[1]) }}
 *  The table is built once, at import, so translating there would freeze the
 *  language it happened to load in; the test still finds the key by this
 *  name. Same idea as gettext's N_(). */
export const N_ = text => text

/** navigator.languages → one of ours. Any Chinese that is not Simplified is
 *  Traditional (zh-HK, zh-MO, plain zh-Hant); anything else English. */
export function detectLocale(langs) {
  for (const raw of langs || []) {
    const l = String(raw).toLowerCase()
    if (l === 'zh-cn' || l === 'zh-sg' || l.startsWith('zh-hans')) return 'zh-CN'
    if (l.startsWith('zh')) return 'zh-TW'
    if (l.startsWith('en')) return 'en'
  }
  return 'en'
}

export function setLocale(code) {
  if (!KNOWN.has(code)) return
  i18n.locale = code
  if (typeof document !== 'undefined') document.documentElement.lang = code
  try { localStorage.setItem(STORE_KEY, code) } catch { /* private mode */ }
}

/** Before sign-in: what this browser last used, else what it asks for. */
export function initialLocale() {
  let saved = null
  try { saved = localStorage.getItem(STORE_KEY) } catch { /* private mode */ }
  if (KNOWN.has(saved)) return saved
  return detectLocale(typeof navigator !== 'undefined' ? navigator.languages : [])
}
