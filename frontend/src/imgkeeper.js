// Which copy of a duplicate image to keep. Pure, so tests/imgkeeper.test.mjs
// can run it without the API or the store.
//
// The first tie-breaker that isolates one file wins; if none does the group
// gets no suggestion at all rather than a guess:
//
//   location → no watermark → more pixels → smaller watermark → more bytes → older
//
// Watermarks (`f.wm`, share of the picture covered, from the scan; null when
// it could not be told) sit in two places on purpose. "Clean beats marked"
// comes before pixels — the user's rule. "Smaller beats bigger" comes after:
// placed before pixels it kept an 899×1200 copy over a 2160×2880 one in five
// Misa_av groups, both watermarked, over a difference of under 1% of the
// picture. And "more bytes" now comes after both, because a re-saved copy
// with a watermark burned in is routinely 2-3x the bytes of the clean one —
// that tie-breaker alone picked the watermarked copy in 42 of 52 groups.

export const CLEAN_MAX = 0.001      // same as backend watermark.CLEAN_MAX
export const SMALLER_RATIO = 0.7    // "smaller" must be clearly smaller

function locPrio(label) {
  if ((label || '').startsWith('歸檔')) return 3
  if (label === '待分類') return 2
  return 1
}

const known = f => f.wm !== null && f.wm !== undefined
export const isClean = f => known(f) && f.wm < CLEAN_MAX

/** One step of the ladder: narrow to `pick` if it is a real narrowing. */
function narrow(c, pick) {
  return pick.length && pick.length < c.length ? pick : c
}

export function suggestKeeper(files) {
  let c = files.filter(f => !f.deleted)
  if (c.length < 2) return { keeper: c[0] || null, certain: true, why: '' }
  const done = why => (c.length === 1 ? { keeper: c[0], certain: true, why } : null)

  const top = Math.max(...c.map(e => locPrio(e.label)))
  c = c.filter(e => locPrio(e.label) === top)
  let r = done('位置'); if (r) return r

  // clean beats marked — only when something here was actually measured as
  // marked; a group where nothing could be told skips the step
  if (c.some(f => known(f) && !isClean(f))) {
    c = narrow(c, c.filter(isClean))
    r = done('無浮水印'); if (r) return r
  }

  // biggest picture wins — a re-saved copy is almost always the smaller one
  const px = e => (e.width || 0) * (e.height || 0)
  const maxPx = Math.max(...c.map(px))
  if (maxPx > 0) c = narrow(c, c.filter(e => px(e) === maxPx))
  r = done('像素較多'); if (r) return r

  // smaller watermark — every remaining copy must be measured and marked
  if (c.every(f => known(f) && !isClean(f))) {
    const lo = Math.min(...c.map(f => f.wm))
    const rest = c.filter(f => f.wm > lo)
    if (rest.length && lo < SMALLER_RATIO * Math.min(...rest.map(f => f.wm))) {
      c = c.filter(f => f.wm === lo)
      r = done('浮水印較小'); if (r) return r
    }
  }

  // same pixels, so prefer the one that spent more bytes on them
  const maxBytes = Math.max(...c.map(e => e.size || 0))
  c = narrow(c, c.filter(e => (e.size || 0) === maxBytes))
  r = done('檔案較大'); if (r) return r

  const minT = Math.min(...c.map(e => e.mtime || 0))
  c = narrow(c, c.filter(e => (e.mtime || 0) === minT))
  r = done('較舊'); if (r) return r

  return { keeper: null, certain: false, why: '' }
}

/** Card label for one file's watermark measurement, or '' if none. */
export function wmLabel(f) {
  if (!known(f)) return ''
  if (isClean(f)) return '無浮水印'
  const pct = f.wm * 100
  return `浮水印 ${pct < 0.1 ? '<0.1' : pct.toFixed(1)}%`
}
