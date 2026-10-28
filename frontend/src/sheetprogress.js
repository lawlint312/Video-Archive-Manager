// Turning /api/sheet/status into a progress bar.
//
// Pure, so it can be tested without a browser
// (`node tests/sheetprogress.test.mjs`).
//
// The server sends { total, ready, building: {index, done, of} | null,
// prefetching }. Two things make this less obvious than it looks:
//
//   * `ready` counts *cached* sheets and the cache has a size limit, so it can
//     go DOWN between polls. Anything that assumes it only grows will show the
//     bar walking backwards, or divide by a stale total.
//   * Counting whole sheets alone makes the bar jump once every ~2 s. The
//     sheet being built reports its own 12 frames, so folding that in gives a
//     bar that actually moves.

/** Sheets finished, as a fraction — including the part-built one. */
export function doneSheets(s) {
  if (!s || !s.total || s.total <= 0) return null
  let done = Math.max(0, Math.min(s.total, s.ready || 0))
  const b = s.building
  // Only count the in-flight sheet if it is one we have NOT already counted:
  // between the last frame landing and the file appearing, `building` and
  // `ready` briefly describe the same sheet, and adding them double-counts.
  if (b && b.of > 0 && b.index >= done && done < s.total) {
    done += Math.max(0, Math.min(1, (b.done || 0) / b.of))
  }
  return Math.min(done, s.total)
}

/** 0-100 for the bar, or null when there is nothing honest to draw. */
export function pct(s) {
  const d = doneSheets(s)
  if (d === null) return null
  return Math.max(0, Math.min(100, (d / s.total) * 100))
}

/** True once every sheet exists, so the bar can disappear. */
export function isComplete(s) {
  return !!s && s.total > 0 && s.ready >= s.total
}

/** "已產生 8 / 20 張", or the single-sheet case, which needs no count. */
export function label(s) {
  if (!s || !s.total) return ''
  if (s.total === 1) return s.ready >= 1 ? '已產生' : '產生中…'
  return `已產生 ${Math.min(s.ready, s.total)} / ${s.total} 張`
}

/**
 * Remaining seconds, from a rolling rate of sheets per second.
 *
 * Deliberately not "sheets so far / elapsed": the viewer is often opened on a
 * video that is already half cached, and counting those instant sheets as
 * work done produces an absurd rate and an ETA of nearly zero. Only the
 * *change* since the window opened is evidence about speed.
 */
export function createEta({ window: win = 20000, minSamples = 3 } = {}) {
  let samples = []
  return {
    /** @returns {number|null} seconds remaining */
    push(s, now = Date.now()) {
      const d = doneSheets(s)
      if (d === null) return null
      // A drop means the cache evicted something. The rate we measured is
      // about a different quantity now, so start over rather than report a
      // negative or wildly inflated estimate.
      const last = samples[samples.length - 1]
      if (last && d < last.d - 0.001) samples = []
      samples.push({ t: now, d })
      samples = samples.filter(x => now - x.t <= win)
      if (samples.length < minSamples) return null
      const first = samples[0], cur = samples[samples.length - 1]
      const dt = (cur.t - first.t) / 1000
      const dd = cur.d - first.d
      if (dt <= 0 || dd <= 0) return null
      const remain = s.total - cur.d
      if (remain <= 0) return 0
      return remain / (dd / dt)
    },
    reset() { samples = [] },
  }
}
