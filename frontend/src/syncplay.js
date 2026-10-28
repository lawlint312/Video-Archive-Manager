/**
 * Keeping two <video> elements playing in step.
 *
 * Extracted so the two rules that matter can be tested, because both were
 * learned the hard way and both look like details someone would "simplify".
 *
 * 1. Echoes are suppressed by a *deadline*, not by a flag cleared on the next
 *    tick. Writing to a video fires its own events, and `seeked` in particular
 *    only arrives once the browser has finished seeking — tens of ms later. A
 *    next-tick flag is already clear by then, so the echo goes through and the
 *    two elements seek each other back and forth for ever. That showed up as
 *    "it keeps playing and pausing".
 *
 * 2. Small drift is corrected by nudging the playback *rate*, never by
 *    seeking. Seeking a playing video stalls its decoder, which creates more
 *    drift, which triggers another seek — the same stutter by a different
 *    route. A hard seek is only for a gap no nudge could close.
 */

export const GUARD_MS = 500
export const DRIFT = 0.08       // start nudging past this many seconds
export const RESYNC = 1.5       // past this, nudging cannot catch up
export const NUDGE = 0.05       // ±5% — inaudible, and closes 0.08 s in ~2 s

/** Deadline-based echo suppression. `now` is injectable for tests. */
export function echoGuard(now = () => performance.now(), ms = GUARD_MS) {
  const until = new WeakMap()
  return {
    hold(el) { until.set(el, now() + ms) },
    held(el) { return now() < (until.get(el) || 0) },
  }
}

/**
 * What to do about a follower that is `drift` seconds behind (negative =
 * ahead), given the driver's current rate.
 *
 *   { kind: 'seek' }            gap too large to nudge away
 *   { kind: 'rate', rate }      speed the follower up or slow it down
 *   { kind: 'none' }            close enough; leave it alone
 *
 * The dead band matters: correcting every frame means the rate never settles,
 * and a rate that never settles is audible on the soundtrack.
 */
export function driftAction(drift, baseRate = 1,
                            { drift: d = DRIFT, resync = RESYNC,
                              nudge = NUDGE } = {}) {
  const mag = Math.abs(drift)
  if (mag > resync) return { kind: 'seek' }
  if (mag < d / 2) return { kind: 'rate', rate: baseRate }
  if (mag > d) return { kind: 'rate', rate: baseRate * (drift > 0 ? 1 + nudge : 1 - nudge) }
  return { kind: 'none' }
}
