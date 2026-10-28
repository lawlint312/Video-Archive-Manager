// Following a convert scan, outside the dialog (state in store.convertScan).
//
// The scan runs on the server and saves itself to the history when it ends.
// Polling it from inside ConvertDialog meant closing the dialog either lost
// the status bar or, kept alive, left a second loop running once the dialog
// was reopened. Here there is one loop per scan, whoever opens the dialog.
import { api, errText } from './api'
import { patiently } from './jobfollow'
import { store, setBusy } from './store'

const POLL_MS = 600

/** Start following `jobId` (no-op if it is already being followed). */
export function followScan(jobId, options, progress = '', stopping = false) {
  const s = store.convertScan
  if (s.jobId === jobId) return
  Object.assign(s, { jobId, options, progress, stopping, result: null, error: '' })
  loop(jobId)
}

async function loop(jobId) {
  const s = store.convertScan
  const mine = () => s.jobId === jobId    // a newer scan, or another account
  try {
    while (true) {
      await new Promise(r => setTimeout(r, POLL_MS))
      if (!mine()) return
      // patiently: a restart brings the scan back under this same id
      const j = await patiently(() => api(`/api/convert/job/${jobId}`))
      if (!mine()) return
      s.progress = j.progress || ''
      setBusy(j.progress ? '轉檔掃描：' + j.progress : '')
      if (j.status === 'running') continue
      if (j.status === 'error') throw { detail: j.error || '執行失敗' }
      // anything else is finished — 'cancelled' included (see ConvertDialog)
      if (j.result == null) throw { detail: `工作結束但沒有結果（${j.status}）` }
      s.result = j.result
      return
    }
  } catch (e) {
    if (mine()) s.error = errText(e)
  } finally {
    if (mine()) {
      s.jobId = null
      setBusy('')
    }
  }
}

/** Ask the scan to stop; it finishes the files in hand and keeps what it found. */
export async function stopScan() {
  const s = store.convertScan
  if (!s.jobId) return
  s.stopping = true
  try { await api(`/api/jobs/${s.jobId}/cancel`, { method: 'POST' }) }
  catch (e) { s.stopping = false; throw e }
}

/** The result that arrived, handed over once. */
export function takeResult() {
  const s = store.convertScan
  const r = s.result
  s.result = null
  return r
}
