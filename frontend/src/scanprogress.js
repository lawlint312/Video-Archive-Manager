// Turning the server's progress object into something a person can read.
//
// Shared by both duplicate scans, and pure so it can be tested without a
// browser (`node tests/scanprogress.test.mjs`).
//
// The server sends { phase, phases, label, done, total, name, hint, eta }.
// `total` is null when the size genuinely is not knowable yet — that is not an
// error state and must not render as 0 or NaN, which is exactly what a naive
// `done/total*100` produces during the folder walk.

/** Percentage 0-100, or null when there is nothing honest to draw. */
export function pct(p) {
  if (!p || !p.total || p.total <= 0) return null
  return Math.max(0, Math.min(100, Math.round((p.done / p.total) * 100)))
}

/** Remaining time, rounded to the precision the number deserves.
 *
 *  Never "2:47". The estimate comes from a rolling rate over a NAS, so its
 *  own error is larger than the digits that would imply — and a countdown
 *  that visibly disagrees with itself is worse than a coarse one. */
export function fmtEta(sec) {
  if (sec == null || !isFinite(sec) || sec < 0) return ''
  if (sec < 10) return '快好了'
  if (sec < 90) return `約 ${Math.round(sec / 5) * 5} 秒`
  const min = Math.round(sec / 60)
  if (min < 60) return `約 ${min} 分鐘`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `約 ${h} 小時 ${m} 分` : `約 ${h} 小時`
}

export function fmtCount(n) {
  return (n ?? 0).toLocaleString('en-US')
}

/** The counter: "1,204 / 3,318" — or just the running count when open-ended.
 *
 *  An `approx` total is last run's file count, and the library grows: writing
 *  "16,500 / 16,204" states a limit that is being exceeded on screen. The bar
 *  may still use the estimate; the number must not claim to be one. */
export function fmtProgressCount(p) {
  if (!p) return ''
  if (!p.total || p.approx) return `已找到 ${fmtCount(p.done)}`
  return `${fmtCount(p.done)} / ${fmtCount(p.total)}`
}

/** One short line for the status bar, which has no room for a bar.
 *
 *  A plain string is passed through untouched: the other jobs (convert,
 *  archive, face index) still report that way, and a shared status bar must
 *  not render "[object Object]" for them. */
export function fmtProgressLine(p) {
  if (!p) return ''
  if (typeof p === 'string') return p
  const bits = []
  if (p.phases > 1) bits.push(`階段 ${p.phase}/${p.phases}`)
  if (p.label) bits.push(p.label)
  bits.push(fmtProgressCount(p))
  const eta = fmtEta(p.eta)
  if (eta) bits.push(eta)
  return bits.join(' · ')
}
