// Duplicate-scan state machine + keep-suggestion logic.
//
// The polling loop lives here rather than inside VideoDupPanel so that closing
// the dialog does not abandon a running scan: the report is rebuilt from
// store.dedup whenever the dialog is reopened.
import { api, errText } from './api'
import { patiently } from './jobfollow'
import { store, setStatus, setBusy } from './store'
import { fmtProgressLine } from './scanprogress'

// ── keep suggestion (ported from the desktop app) ───────────────────────────
// Walks a ladder of tie-breakers; the first one that isolates a single file
// wins. If none of them do, the group is marked "needs a human" and gets no
// suggestion — that is deliberate, a wrong auto-suggestion costs a video.
function locPrio(label) {
  if ((label || '').startsWith('歸檔')) return 3
  if (label === '待分類') return 2
  return 1
}

// how much bigger a file has to be before size decides (see below)
export const BIGGER = 1.1

export function suggestKeeper(files) {
  let c = files.filter(f => !f.deleted)
  // Only one copy left — the group is settled, not something to ask about.
  if (c.length < 2) return { keeper: c[0] || null, certain: true }

  const top = Math.max(...c.map(e => locPrio(e.label)))
  c = c.filter(e => locPrio(e.label) === top)
  if (c.length === 1) return { keeper: c[0], certain: true }

  const maxDur = Math.max(...c.map(e => e.duration || 0))
  if (maxDur > 0) {
    const longer = c.filter(e => maxDur - (e.duration || 0) <= 5)
    if (longer.length && longer.length < c.length) c = longer
    if (c.length === 1) return { keeper: c[0], certain: true }
  }

  const maxPx = Math.max(...c.map(e => (e.width || 0) * (e.height || 0)))
  if (maxPx > 0) {
    const hi = c.filter(e => (e.width || 0) * (e.height || 0) === maxPx)
    if (hi.length && hi.length < c.length) c = hi
    if (c.length === 1) return { keeper: c[0], certain: true }
  }

  // .mp4 before "older": a file's date is when this copy arrived, not which
  // one is the original, while an .mp4 is the copy the browser can play.
  // Same picture as `.mov` 2022 vs `.mp4` 2023 used to keep the .mov.
  const mp4 = c.filter(e => e.name.toLowerCase().endsWith('.mp4'))
  if (mp4.length && mp4.length < c.length) c = mp4
  if (c.length === 1) return { keeper: c[0], certain: true }

  // Clearly larger before "older" (2026-10-24, the user's choice): same
  // length and resolution, so a file 10%+ bigger carries more bitrate, and it
  // is usually the original — `ScreenRecording_04-04-2025….mp4` (36 MB, dated
  // today because it was just copied in) against `Denise_11.mp4` (19.8 MB,
  // dated three days after the recording: a re-encode) used to keep the small
  // one. Under 10% is noise, and "older" decides as before. Simulated on the
  // 131 groups in the scan history: 7 change, every one to the bigger file.
  const maxS = Math.max(...c.map(e => e.size || 0))
  if (maxS > 0) {
    const big = c.filter(e => (e.size || 0) * BIGGER >= maxS)
    if (big.length && big.length < c.length) c = big
    if (c.length === 1) return { keeper: c[0], certain: true }
  }

  const minT = Math.min(...c.map(e => e.mtime || 0))
  const oldest = c.filter(e => (e.mtime || 0) === minT)
  if (oldest.length && oldest.length < c.length) c = oldest
  if (c.length === 1) return { keeper: c[0], certain: true }

  return { keeper: null, certain: false }
}

/** Recompute keeper/certain for one group (after a delete changes the field). */
export function rescore(g) {
  const { keeper, certain } = suggestKeeper(g.files)
  g.keeper = keeper ? keeper.path : null
  g.certain = certain
}

// ── formatting ──────────────────────────────────────────────────────────────
export function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(1) + ' MB'
}

export function fmtDur(sec) {
  if (!sec || sec <= 0) return '--:--'
  const s = Math.floor(sec), h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60), ss = s % 60
  const pad = n => String(n).padStart(2, '0')
  return h ? `${h}:${pad(m)}:${pad(ss)}` : `${pad(m)}:${pad(ss)}`
}

export function fmtDate(mtime) {
  if (!mtime) return '?'
  const d = new Date(mtime * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-` +
         `${String(d.getDate()).padStart(2, '0')}`
}

// ── the scan ────────────────────────────────────────────────────────────────
// Bumped by everything that puts a report on screen on purpose; a saved
// report requested before that is older and must not replace it.
let screenGen = 0

export async function runScan({ roots, tier2, threshold }) {
  const d = store.dedup
  if (d.running) return
  screenGen++
  d.running = true
  d.error = ''
  d.groups = null
  d.tier2 = !!tier2
  d.savedAt = null
  d.partial = false
  d.historyId = null
  forgetPicking('video')
  d.stopping = false
  d.progress = null
  setBusy('重複掃描：啟動中…')
  try {
    const { job_id } = await api('/api/dedup/scan', {
      method: 'POST',
      body: JSON.stringify({ roots, tier2, threshold }),
    })
    d.jobId = job_id
    await follow(job_id)
  } catch (e) {
    d.error = '掃描失敗：' + errText(e)
    setStatus(d.error, 'warn')
  } finally {
    d.running = false
    d.stopping = false
    d.progress = null
    setBusy('')
  }
}

/** Ask the server to stop. Cooperative: the current file finishes first, so
 *  the button reports "停止中…" rather than pretending it was instant. */
export async function stopScan() {
  const d = store.dedup
  if (!d.running || !d.jobId || d.stopping) return
  d.stopping = true
  try {
    await api(`/api/jobs/${d.jobId}/cancel`, { method: 'POST' })
    setStatus('已要求停止，正在收尾…', 'info')
  } catch (e) {
    d.stopping = false
    setStatus('停止失敗：' + errText(e), 'warn')
  }
}

/** Pick a scan back up after a reload — the server kept working. */
export async function resumeScan(jobId) {
  const d = store.dedup
  if (d.running) return
  screenGen++
  d.running = true
  d.jobId = jobId
  d.error = ''
  d.groups = null
  d.partial = false
  d.stopping = false
  setStatus('接回進行中的重複掃描…', 'info')
  try {
    await follow(jobId)
  } catch (e) {
    d.error = '掃描失敗：' + errText(e)
  } finally {
    d.running = false
    d.stopping = false
    d.progress = null
    setBusy('')
  }
}

/** Poll one scan job to completion and turn the result into the report. */
async function follow(job_id) {
  const d = store.dedup
  while (true) {
    await new Promise(r => setTimeout(r, 700))
    // patiently: through a restart of the service the scan comes back
    // under this same id (jobfollow.js), so a failed poll is not the end
    const j = await patiently(() => api(`/api/dedup/job/${job_id}`))
    d.progress = j.progress || null
    if (j.progress) setBusy('重複掃描：' + fmtProgressLine(j.progress))
    // 'cancelled' carries a real, partial result — the Tier 1 groups it did
    // find are as valid as any. Treating it as a failure would throw away
    // work the user waited for.
    if (j.status === 'done' || j.status === 'cancelled') {
      d.partial = j.status === 'cancelled'
      d.groups = j.result.map(toGroup)
      const n = d.groups.length
      setStatus(d.partial
        ? `重複掃描已停止：目前找到 ${n} 組（只涵蓋已完成的部分）`
        : (n ? `重複掃描完成：發現 ${n} 組重複`
             : '重複掃描完成：未發現重複影片'),
        d.partial || n ? 'warn' : 'ok')
      break
    }
    if (j.status === 'error') {
      d.error = j.error || '掃描失敗'
      setStatus('重複掃描失敗：' + d.error, 'warn')
      break
    }
  }
}

/** Server shape → report shape (adds the per-file/per-group working state). */
function toGroup(g) {
  const files = g.files.map(f => ({ ...f, deleted: !!f.deleted }))
  const grp = { tier: g.tier, files, touched: false,
                distance: g.distance ?? null }
  rescore(grp)
  return grp
}

/** Reopen the last scan saved on the server.
 *
 *  A scan over a big library costs minutes, and a few hundred groups is not
 *  something anyone finishes in one sitting — losing it to a page refresh made
 *  the whole report a use-it-now-or-redo-it affair. Files deleted since the
 *  scan come back already marked, so the report reflects the work done. */
export async function loadSavedReport() {
  const d = store.dedup
  if (d.running || d.groups) return
  const gen = screenGen
  try {
    const { report } = await api('/api/dedup/report')
    // Checked again on arrival. The saved report can be slow (it checks every
    // file still exists), and a scan started meanwhile is newer: in the image
    // scan the old report replaced a fresh one mid-deletion (2026-10-26).
    if (gen !== screenGen || d.running || d.groups) return
    if (!report || !report.groups.length) return
    d.groups = report.groups.map(toGroup)
    d.tier2 = !!report.tier2
    d.savedAt = report.at
    d.partial = !!report.partial
  } catch { /* no saved report is not an error */ }
}

/** Open one saved scan from the history (see DupHistory.vue). */
export async function openHistoryReport(id) {
  const d = store.dedup
  screenGen++
  const { report } = await api(`/api/dedup/history/video/${encodeURIComponent(id)}`)
  d.groups = (report?.groups || []).map(toGroup)
  d.tier2 = !!report?.tier2
  d.savedAt = report?.at ?? null
  d.partial = !!report?.partial
  d.historyId = id
  forgetPicking('video')
}

// "重新掃描" means "I'm done with this report for now", so closing and
// reopening the dialog stays on the picker instead of springing the old
// report back. A plain flag, cleared by the two things that put a report on
// screen on purpose -- starting a scan, opening one from the history. (Tying
// it to the report's timestamp failed right after a fresh scan: that report
// has no saved timestamp in this session yet.) localStorage, so a reload
// keeps it too.
const PICK_KEY = { video: 'vam.dedup.picking', image: 'vam.imgdedup.picking' }
export function rememberPicking(kind) {
  try { localStorage.setItem(PICK_KEY[kind], '1') } catch { /* private mode */ }
}
export function forgetPicking(kind) {
  try { localStorage.removeItem(PICK_KEY[kind]) } catch { /* private mode */ }
}
export function wasPicking(kind) {
  try { return localStorage.getItem(PICK_KEY[kind]) === '1' } catch { return false }
}

/** How many groups still need a human decision (Tier 2 always does). */
export function manualCount(groups) {
  return (groups || []).filter(g => g.tier === 2 || !g.certain).length
}
