// Searching video filenames from the sidebar.
//
// The whole index is fetched once (16,204 rows, ~0.2 MB gzipped) and searched
// here: a full substring scan measures about a millisecond, so results appear
// as you type with no debounce and no request per keystroke.
//
// That millisecond needs the lowercased and normalised strings worked out once
// per index (`keysFor`), not per keystroke. Recomputing them inside the scan
// cost 18 ms whenever a query matched little or nothing — a full pass with a
// regex replace on 32,000 strings — which a phone turns into 50–90 ms a key.
//
// Rows arrive as [name, character, dirIndex, isInbox, title, co-star names]
// (the names are co-stars marked with no character folder) with a deduplicated
// directory list — 16,204 videos live in 1,288 folders, so storing an index
// instead of a path per row saves a quarter of the transfer.
import { reactive, markRaw, toRaw } from 'vue'
// explicit extension, unlike the rest of the codebase: node's ESM resolver
// needs it, and tests/videosearch.test.mjs imports this module directly
import { api } from './api.js'

export const videoIndex = reactive({
  rows: [],        // [name, char, dirIdx, inbox, title]
  dirs: [],
  ready: false,
  loading: false,
})

export async function loadVideoIndex(force = false) {
  if (videoIndex.loading) return
  if (videoIndex.ready && !force) return
  videoIndex.loading = true
  try {
    const d = await api('/api/search/index')
    videoIndex.dirs = d.dirs || []
    // raw: 16,000 rows are only ever replaced whole, never edited, so a deep
    // proxy per row buys nothing and slows every read of the scan
    videoIndex.rows = markRaw(d.v || [])
    videoIndex.ready = true
    // the server answered from a stale cache and is rebuilding; come back
    if (d.stale) setTimeout(() => loadVideoIndex(true), 4000)
  } catch { /* search simply stays name-only */ } finally {
    videoIndex.loading = false
  }
}

export function clearVideoIndex() {
  videoIndex.rows = []
  videoIndex.dirs = []
  videoIndex.ready = false
}

export function fullPath(row) {
  const dir = videoIndex.dirs[row[2]] || ''
  const sep = dir.includes('\\') ? '\\' : '/'
  return dir + sep + row[0]
}

/**
 * Catalogue numbers are written half a dozen ways — STARS-786, STARS786,
 * "stars 786", stars_786 — so a second, punctuation-free comparison runs
 * alongside the plain one. Without it, typing the id the way you remember it
 * usually finds nothing.
 */
export function normalize(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9぀-ヿ一-鿿]+/g, '')
}

// [name, title, normalised name without its extension, normalised title,
//  character] — lowercased, one per row, in row order
let prepared = { rows: null, length: 0, keys: [] }

function keysFor(rows) {
  if (prepared.rows !== rows || prepared.length !== rows.length) {
    prepared = {
      rows, length: rows.length,
      keys: rows.map(r => {
        const name = r[0] || '', title = r[4] || ''
        return [name.toLowerCase(), title.toLowerCase(),
                normalize(name.replace(/\.[^.]+$/, '')), normalize(title),
                (r[1] || '').toLowerCase(), (r[5] || '').toLowerCase()]
      }),
    }
  }
  return prepared.keys
}

// most JAV filenames are a bare catalogue number, so the film title is often
// the only searchable text the file has
function inFile(k, q, qn) {
  return k[0].includes(q) || k[1].includes(q) || k[5].includes(q)
    || (qn.length >= 3 && (k[2].includes(qn) || k[3].includes(qn)))
}

/**
 * How well a row matches — lower is better; null is no match.
 *
 *   0  the whole name is the catalogue number typed (stars786 → STARS-786.mp4)
 *   1  the name starts with it
 *   2  the name or title contains it
 *   3  every word of a several-word query is somewhere in the row
 *
 * The several-word rule is a fallback, not the first test: "stars 786" must
 * still find STARS-786 as a whole (rank 0), and only a query that matches
 * nothing as one string gets split. Words may also match the character, so
 * "三浦 STARS" finds her STARS releases — but one of them has to match the
 * file itself, or every file of a character would match its name alone.
 */
function score(k, q, qn, words) {
  if (inFile(k, q, qn)) {
    if (qn.length >= 3 && k[2] === qn) return 0
    if (k[0].startsWith(q) || (qn.length >= 3 && k[2].startsWith(qn))) return 1
    return 2
  }
  if (words.length < 2) return null
  let fileWord = false
  for (const [w, wn] of words) {
    if (inFile(k, w, wn)) { fileWord = true; continue }
    if (!k[4].includes(w)) return null
  }
  return fileWord ? 3 : null
}

const byRank = (a, b) => a.s - b.s || a.i - b.i

/**
 * Videos matching `q`, grouped by character, best matches first.
 *
 * `limit` caps the work for a short query that matches everything;
 * `perChar` caps what one character contributes, because a name that matches
 * 200 files should not bury every other result.
 */
export function searchVideos(q, { limit = 400, perChar = 3 } = {}) {
  const needle = (q || '').trim().toLowerCase()
  if (needle.length < 2) return { byChar: new Map(), inbox: [], total: 0 }
  const qn = normalize(needle)
  const words = needle.split(/\s+/).filter(Boolean).map(w => [w, normalize(w)])

  // toRaw: the tests assign plain arrays, which the reactive object wraps
  const rows = toRaw(videoIndex.rows)
  const keys = keysFor(rows)
  const found = new Map()           // character → [{ r, s, i }]
  const inbox = []
  let total = 0
  for (let i = 0; i < rows.length; i++) {
    const s = score(keys[i], needle, qn, words)
    if (s === null) continue
    total++
    const r = rows[i]
    if (r[3]) { inbox.push({ r, s, i }); continue }
    let list = found.get(r[1])
    if (!list) found.set(r[1], list = [])
    list.push({ r, s, i })
    if (total >= limit) break
  }

  const byChar = new Map()
  for (const [name, list] of found) {
    byChar.set(name, { shown: list.sort(byRank).slice(0, perChar).map(h => h.r),
                       count: list.length })
  }
  return { byChar, total,
           inbox: inbox.sort(byRank).slice(0, perChar * 4).map(h => h.r) }
}
