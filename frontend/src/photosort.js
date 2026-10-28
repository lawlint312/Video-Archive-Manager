/**
 * Ordering the profile grid: 最近新增 / 名稱 / 大小 / 相似.
 *
 * Pure, so the rules can be tested without a browser — `mount.test.mjs`
 * renders the profile tab but never clicks a sort button, and a sort that is
 * wrong looks exactly like a sort that is right until you read the names.
 */

/**
 * Name comparison that reads digit runs as numbers, the way File Explorer
 * does. A plain `localeCompare` put `Rocksylight_21` after `Rocksylight_2080`
 * (measured on the video grid, which used it), because it compares `1` with
 * `0` character by character. Case-insensitive.
 */
const COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })
export function naturalCompare(a, b) {
  return COLLATOR.compare(a || '', b || '')
}

export function baseName(path) {
  return (path || '').split(/[\\/]/).pop()
}

export const NATURAL_DIR = { newest: 'desc', name: 'asc', size: 'desc', similar: 'desc' }

function plain(items, by, dir) {
  const s = [...items]
  if (by === 'name') s.sort((a, b) => naturalCompare(baseName(a.path), baseName(b.path)))
  else if (by === 'size') s.sort((a, b) => (a.size || 0) - (b.size || 0))
  else s.sort((a, b) => (a.mtime || 0) - (b.mtime || 0))
  return dir === 'desc' ? s.reverse() : s
}

/**
 * 相似: time order, with each group pulled together at the position of its
 * first member in that order (the newest one, in the natural direction).
 *
 * Photos with no group — most of them, since dHash only matches near-identical
 * framing — keep exactly the place time order gives them. IG videos have no
 * hash and are never in a group, so they stay put too.
 *
 * `groups` is `{ path: groupId }`. Direction flips the time order and
 * therefore also which member anchors a group, so the result is always "the
 * order you chose, with bursts kept together", never a separate ordering.
 */
function similar(items, dir, groups) {
  const base = plain(items, 'newest', dir)
  const g = groups || {}
  const members = new Map()
  for (const m of base) {
    const id = g[m.path]
    if (id === undefined || id === null) continue
    if (!members.has(id)) members.set(id, [])
    members.get(id).push(m)
  }
  const out = []
  const emitted = new Set()
  for (const m of base) {
    const id = g[m.path]
    if (id === undefined || id === null) { out.push(m); continue }
    if (emitted.has(id)) continue
    emitted.add(id)
    out.push(...members.get(id))
  }
  return out
}

export function sortMedia(items, by, dir, groups) {
  if (!Array.isArray(items)) return []
  if (by === 'similar') return similar(items, dir, groups)
  return plain(items, by, dir)
}

/** Next state when a sort button is clicked: same field flips, new starts natural. */
export function nextSort(cur, by) {
  if (cur.by === by) return { by, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
  return { by, dir: NATURAL_DIR[by] || 'desc' }
}

/**
 * What each slider does, and where the current value sits.
 *
 * The user asked for "bigger does what, smaller does what, use what" rather
 * than a verdict on the current value alone. The two sliders run in opposite
 * directions — framing is a distance (bigger = looser), colour is a
 * similarity (bigger = stricter) — so each guide says so outright.
 *
 * Bands are the measured ones (core/photosimilar.py): framing 41-60 and 61-80
 * sampled pairs were one sitting, 81+ unrelated; colour 93%+ sampled pairs
 * were one outfit, 90-93% about half.
 */
export const THRESHOLD_GUIDE =
  '比對構圖與背景，找出連拍。數字越小越嚴格，越大越寬鬆；建議 60～75。'
export const COLOR_GUIDE =
  '比對整張照片的配色（含頭髮、衣服、背景）。數字越大越嚴格，越小越寬鬆，方向跟上面相反；建議 93～95。'

export function thresholdHint(t) {
  if (t < 60) return { level: 'safe', text: '目前偏嚴格：只有幾乎一模一樣的連拍會排在一起' }
  if (t <= 75) return { level: 'ok', text: '目前在建議範圍' }
  return { level: 'risky', text: '目前偏寬鬆：可能把構圖相像但不相干的照片排在一起' }
}

export function colorHint(v) {
  if (v >= 96) return { level: 'safe', text: '目前偏嚴格：只併進配色幾乎一樣的照片' }
  if (v >= 93) return { level: 'ok', text: '目前在建議範圍' }
  return { level: 'risky', text: '目前偏寬鬆：可能把配色相近的其他造型併進來' }
}
