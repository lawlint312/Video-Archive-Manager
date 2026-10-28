// Which image-duplicate groups one 「執行建議」 press deletes from.
//
// It used to be byte-identical groups only. A real report (812 groups) had 0
// of those and 661 at distance 0 — the same picture saved twice — so every
// group was a click. The user now picks how far the batch reaches (0–20,
// default 0, not remembered). Calibration from the scan's own measurements:
// 0–14 no wrong pairs, 15–19 about 1 in 10, 20 about 1 in 3.
//
// Pure, so `node tests/imgbatch.test.mjs` can pin it: what a batch deletes is
// exactly what the confirmation said it would.

export const BATCH_MAX = 20

/** True when the live files are byte-identical copies only. */
export function isExactOnly(g) {
  const live = g.files.filter(f => !f.deleted)
  if (live.length < 2) return false
  return live.every(f => f.exact === live[0].exact)
}

/** The group's distance over the files still on disk (each file's distance is
 *  measured from the group's anchor, the number the calibration used). */
export function liveDistance(g) {
  const live = g.files.filter(f => !f.deleted)
  return Math.max(0, ...live.map(f => f.distance ?? g.distance ?? 0))
}

/** What a batch at `max` would do.
 *
 *  A group is in when it still has two files, it is within `max`, and there is
 *  a keeper. Byte-identical groups need no verdict — any copy is the picture —
 *  so one without a keeper keeps its first file. A similar group without a
 *  keeper ("無法判斷") is never batched: it is counted in `undecided`.
 */
export function batchPlan(groups, max) {
  const out = { groups: [], paths: [], bytes: 0, undecided: 0 }
  for (const g of groups) {
    const live = g.files.filter(f => !f.deleted)
    if (live.length < 2) continue
    const exact = isExactOnly(g)
    if (!exact && liveDistance(g) > max) continue
    let keep = live.find(f => f.path === g.keeper)
    if (!keep) {
      if (!exact) { out.undecided++; continue }
      keep = live[0]
    }
    out.groups.push(g)
    for (const f of live) {
      if (f === keep) continue
      out.paths.push(f.path)
      out.bytes += f.size || 0
    }
  }
  return out
}

/** How much to trust a batch at this distance, in the user's words. */
export function batchRisk(max) {
  if (max <= 14) return { level: 'safe', text: '實測無誤判' }
  if (max <= 19) return { level: 'mid', text: '約 1 成會判錯' }
  return { level: 'high', text: '約 3 成會判錯' }
}
