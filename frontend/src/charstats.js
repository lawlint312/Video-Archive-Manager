// Per-character counts/sizes/newest times, fetched once and shared.
//
// The sidebar and the home grid both want these numbers, and the home grid
// remounts every time you navigate back to it — without sharing, each visit
// paid for another full walk of every character folder (2–3 seconds on a NAS).
import { reactive } from 'vue'
import { api } from './api'

export const charStats = reactive({ counts: {}, stats: {}, ready: false })

let inflight = null
let staleTimer = null

export async function loadCharStats(force = false) {
  if (charStats.ready && !force) return charStats
  if (inflight) return inflight
  inflight = api('/api/characters/counts')
    .then(d => {
      charStats.counts = d.counts || {}
      charStats.stats = d.stats || {}
      charStats.ready = true
      // the server answered from an out-of-date cache and is recounting now
      if (d.stale) {
        clearTimeout(staleTimer)
        staleTimer = setTimeout(() => loadCharStats(true), 3000)
      }
      return charStats
    })
    .catch(() => charStats)
    .finally(() => { inflight = null })
  return inflight
}

/** Call after archiving/merging/deleting so the numbers catch up. */
export function refreshCharStats() { return loadCharStats(true) }

/** Drop everything on sign-out: the next account may not see these folders. */
export function clearCharStats() {
  clearTimeout(staleTimer)
  inflight = null
  charStats.counts = {}
  charStats.stats = {}
  charStats.ready = false
}
