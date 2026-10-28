// Image duplicate-scan state machine + keep-suggestion logic.
//
// Deliberately separate from dedup.js rather than parameterised: the two
// scans share a shape but almost none of the judgement. Videos rank by
// duration and container; images rank by pixel count and have a hard notion
// of "byte-identical" that videos do not use.
//
// As with the video scan the polling loop lives here, not in the dialog, so
// closing the dialog does not abandon a scan that costs minutes.
import { api, errText } from './api'
import { store, setStatus, setBusy } from './store'
import { fmtSize, fmtDate, forgetPicking } from './dedup'
import { fmtProgressLine } from './scanprogress'
import { patiently } from './jobfollow'

export { fmtSize, fmtDate }

// The keeper ladder lives in imgkeeper.js (pure, tested on its own).
export { suggestKeeper, wmLabel } from './imgkeeper.js'
import { suggestKeeper } from './imgkeeper.js'

export function rescore(g) {
  const { keeper, certain, why } = suggestKeeper(g.files)
  g.keeper = keeper ? keeper.path : null
  g.certain = certain
  g.why = why
}

/** Files in this group that are byte-identical to another one in it. */
export function exactSets(g) {
  const seen = {}
  for (const f of g.files) {
    if (f.exact === undefined || f.exact === null) continue
    seen[f.exact] = (seen[f.exact] || 0) + 1
  }
  return seen
}

// byte-identical check and the batch plan live in imgbatch.js (pure, tested)
export { isExactOnly } from './imgbatch.js'

export async function runScan({ roots, maxDistance }) {
  const d = store.imgdedup
  if (d.running) return
  screenGen++
  d.running = true
  d.error = ''
  d.groups = null
  d.savedAt = null
  d.maxDistance = maxDistance
  d.partial = false
  d.historyId = null
  forgetPicking('image')
  d.stopping = false
  d.progress = null
  setBusy('圖片重複掃描：啟動中…')
  try {
    const { job_id } = await api('/api/imgdedup/scan', {
      method: 'POST',
      body: JSON.stringify({ roots, max_distance: maxDistance }),
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

/** See `dedup.stopScan` — same contract, same cooperative stop. */
export async function stopScan() {
  const d = store.imgdedup
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

export async function resumeScan(jobId) {
  const d = store.imgdedup
  if (d.running) return
  screenGen++
  d.running = true
  d.jobId = jobId
  d.error = ''
  d.groups = null
  d.partial = false
  d.stopping = false
  setStatus('接回進行中的圖片重複掃描…', 'info')
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

async function follow(job_id) {
  const d = store.imgdedup
  while (true) {
    await new Promise(r => setTimeout(r, 700))
    // patiently: through a restart of the service the scan comes back
    // under this same id (jobfollow.js), so a failed poll is not the end
    const j = await patiently(() => api(`/api/dedup/job/${job_id}`))   // shared job endpoint
    d.progress = j.progress || null
    if (j.progress) setBusy('圖片重複掃描：' + fmtProgressLine(j.progress))
    if (j.status === 'done' || j.status === 'cancelled') {
      d.partial = j.status === 'cancelled'
      d.groups = j.result.map(toGroup)
      const n = d.groups.length
      setStatus(d.partial
        ? `圖片重複掃描已停止：目前找到 ${n} 組（只涵蓋已完成的部分）`
        : (n ? `圖片重複掃描完成：發現 ${n} 組`
             : '圖片重複掃描完成：未發現重複圖片'),
        d.partial || n ? 'warn' : 'ok')
      break
    }
    if (j.status === 'error') {
      d.error = j.error || '掃描失敗'
      setStatus('圖片重複掃描失敗：' + d.error, 'warn')
      break
    }
  }
}

function toGroup(g) {
  const files = g.files.map(f => ({ ...f, deleted: !!f.deleted }))
  const grp = { tier: g.tier, files, touched: false, folder: g.folder || '',
                distance: g.distance ?? null }
  rescore(grp)
  return grp
}

/** Open one saved scan from the history (see DupHistory.vue). */
export async function openHistoryReport(id) {
  const d = store.imgdedup
  screenGen++
  const { report } = await api(`/api/dedup/history/image/${encodeURIComponent(id)}`)
  d.groups = (report?.groups || []).map(toGroup)
  d.savedAt = report?.at ?? null
  d.partial = !!report?.partial
  d.maxDistance = report?.max_distance ?? d.maxDistance
  d.historyId = id
  forgetPicking('image')
}

// Bumped by everything that puts a report on screen on purpose. The saved
// report can take a long time to come back (it checks every file still exists;
// 39 s for a folder on the NAS), and a scan started meanwhile is newer: its
// result was overwritten mid-deletion by the old report once (2026-10-26).
let screenGen = 0

export async function loadSavedReport() {
  const d = store.imgdedup
  if (d.running || d.groups) return
  const gen = screenGen
  try {
    const r = await api('/api/imgdedup/report')
    d.safeDistance = r.safe_distance ?? d.safeDistance
    // checked again on arrival: anything since is newer than this
    if (gen !== screenGen || d.running || d.groups) return
    if (!r.report || !r.report.groups.length) return
    d.groups = r.report.groups.map(toGroup)
    d.savedAt = r.report.at
    d.partial = !!r.report.partial
    d.maxDistance = r.report.max_distance ?? d.maxDistance
  } catch { /* no saved report is not an error */ }
}
