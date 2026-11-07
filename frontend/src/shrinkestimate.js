// How much a re-encode will save, before it is done.
//
// The base is a curve measured on eight files: source bitrate → fraction saved
// at H.264 crf 26, resolution unchanged (the same curve as core/shrink.py,
// which only uses it to sort the scan). Everything the panel lets you change
// is then applied as a factor on the *output* size, because that is how the
// measurements line up:
//
//   crf       output relative to crf 26, from the crf table (two sources):
//             6.1 Mbps 1080p  crf 20 ×1.97, 23 ×1.40, 28 ×0.80
//             52 Mbps 4K      crf 20 ×2.57, 23 ×1.64, 28 ×0.79
//             interpolated on log(bitrate) between the two
//   H.265     ×0.69 — hevc_nvenc kept 16.2% where x264 kept 23.5%, same SSIM
//   max edge  (pixels kept)^0.9 — a 4K clip scaled to 1080 at crf 26 kept 4%
//             where the full-size encode kept 14%
//
// That model was 2.2× too optimistic the first time it met a real file (a
// 32 Mbps 4K film at crf 20: 7.12 GB estimated against 3.23 GB actually
// saved, 2026-11-07), and content — motion, grain — is something a bitrate
// cannot see. So every finished shrink is recorded (core/shrinkstats.py) and
// `calibrate()` turns those into a correction: the geometric mean of
// actual ÷ predicted output, pulled toward 1 while there are only a few, and
// kept per codec, each pulled toward the all-codec figure.

const CURVE = [[1.5, 0.23], [3.3, 0.45], [6.1, 0.62], [6.3, 0.74],
               [13.0, 0.80], [52.0, 0.86]]

const CRF_LO = { mbps: 6.1, f: { 20: 1.97, 23: 1.40, 26: 1, 28: 0.80 } }
const CRF_HI = { mbps: 52, f: { 20: 2.57, 23: 1.64, 26: 1, 28: 0.79 } }
export const H265_FACTOR = 0.69
const EDGE_EXP = 0.9
// how many "neutral" results a correction is blended with: one real result
// moves it a third of the way, six move it three quarters
const PRIOR = 2
const MAX_SAVED = 0.97

export function baseSaving(mbps) {
  if (mbps <= CURVE[0][0]) return CURVE[0][1]
  for (let k = 1; k < CURVE.length; k++) {
    const [a, sa] = CURVE[k - 1], [b, sb] = CURVE[k]
    if (mbps <= b) return sa + (sb - sa) * (mbps - a) / (b - a)
  }
  return CURVE[CURVE.length - 1][1]
}

export function crfFactor(crf, mbps) {
  const lo = CRF_LO.f[crf], hi = CRF_HI.f[crf]
  if (lo == null) return 1
  const x = Math.log(Math.max(mbps, 0.01))
  const a = Math.log(CRF_LO.mbps), b = Math.log(CRF_HI.mbps)
  const w = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return lo + (hi - lo) * w
}

// fraction of pixels kept when the long edge is capped (0 = not capped)
export function pixelsKept(w, h, maxEdge) {
  const long = Math.max(w || 0, h || 0)
  if (!maxEdge || !long || long <= maxEdge) return 1
  return (maxEdge / long) ** 2
}

// output size ÷ source size, before any calibration
export function predictedOut({ mbps, width, height }, { crf, codec, maxEdge }) {
  let out = (1 - baseSaving(mbps)) * crfFactor(crf, mbps)
  if (codec === 'h265') out *= H265_FACTOR
  out *= pixelsKept(width, height, maxEdge) ** EDGE_EXP
  return out
}

function geo(logs, prior, n0 = PRIOR) {
  const s = logs.reduce((a, b) => a + b, 0)
  return Math.exp((s + n0 * Math.log(prior)) / (logs.length + n0))
}

// results: [{ mbps, width, height, crf, codec, max_edge, before, after }]
// → { all, h264, h265, n: { all, h264, h265 } }
export function calibrate(results) {
  const logs = { h264: [], h265: [] }
  for (const r of results || []) {
    if (!(r.before > 0 && r.after > 0 && r.mbps > 0)) continue
    const p = predictedOut(r, { crf: r.crf, codec: r.codec, maxEdge: r.max_edge })
    if (!(p > 0)) continue
    const c = r.codec === 'h265' ? 'h265' : 'h264'
    logs[c].push(Math.log((r.after / r.before) / p))
  }
  const every = [...logs.h264, ...logs.h265]
  const all = geo(every, 1)
  return {
    all,
    h264: geo(logs.h264, all),
    h265: geo(logs.h265, all),
    n: { all: every.length, h264: logs.h264.length, h265: logs.h265.length },
  }
}

// bytes saved for one candidate under the current settings
export function estimateSaving(item, settings, cal) {
  const k = cal ? (cal[settings.codec] ?? cal.all ?? 1) : 1
  const out = Math.min(1, predictedOut(item, settings) * k)
  return Math.round(item.size * Math.min(MAX_SAVED, Math.max(0, 1 - out)))
}
