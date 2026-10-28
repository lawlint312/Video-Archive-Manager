// Background jobs across a restart of the service.
//
// The server now brings interrupted scans back by itself, under the same job
// id (see backend jobs.py). A page that was polling one only has to wait out
// the gap instead of giving up on the first failed request: `patiently`.
//
// Jobs that write files wait for their owner instead, as notices; one
// continued from there is followed in the status bar by `followJob`.
//
// The pure parts are exported for tests/jobfollow.test.mjs — which is why the
// imports spell out their extensions.
import { api, errText } from './api.js'
import { setBusy, setStatus } from './store.js'
import { fmtProgressLine } from './scanprogress.js'

// A restart measured on this machine is 5–15 s from the old process gone to
// the new one answering; the scans are resumed a moment after that. 90 s is
// far beyond it and still short enough that a job that is really gone shows
// as gone without a long wait.
export const RESTART_WINDOW_MS = 90000
const RETRY_MS = 3000

/** What a restart looks like from the page: no answer at all (0), the proxy
 *  with nothing behind it (502–504), or a job id the new process has not
 *  taken back yet (404). Anything else is a real answer and not retried. */
export function looksLikeRestart(e) {
  const s = e?.status
  return s === 0 || s === 404 || s === 502 || s === 503 || s === 504
}

/** `fn()`, tried again through a restart for up to `windowMs`. */
export async function patiently(fn, { windowMs = RESTART_WINDOW_MS, waitMs = RETRY_MS,
                                     sleep = ms => new Promise(r => setTimeout(r, ms)),
                                     now = () => Date.now() } = {}) {
  const t0 = now()
  for (;;) {
    try {
      return await fn()
    } catch (e) {
      if (!looksLikeRestart(e) || now() - t0 >= windowMs) throw e
      await sleep(waitMs)
    }
  }
}

/** One line for any job's progress. The scans report structured progress,
 *  the file jobs a dict of their own shape — or a plain string. */
export function fmtJobProgress(p) {
  if (!p) return ''
  if (typeof p === 'string' || p.phases) return fmtProgressLine(p)
  const bits = []
  const total = p.total ?? p.count
  if (p.index && total) bits.push(`${p.index}/${total}`)
  if (p.name) bits.push(p.name)
  if (p.stage) bits.push(p.stage)
  if (typeof p.pct === 'number' && p.pct > 0) bits.push(`${Math.round(p.pct * 100)}%`)
  else if (p.done && p.total && p.size == null && !p.index) {
    bits.push(`${Math.round(p.done / p.total * 100)}%`)   // a move's bytes
  }
  return bits.join(' · ')
}

/** The status-bar line once a continued job has ended. */
export function jobSummary(label, j) {
  if (j.status === 'error') return { text: `${label}失敗：${j.error || '未知的錯誤'}`, kind: 'warn' }
  const r = j.result || {}
  const ok = r.converted || r.done
  const failed = Array.isArray(r.failed) ? r.failed : []
  if (Array.isArray(ok)) {
    const head = `${label}：完成 ${ok.length} 個`
    if (!failed.length) return { text: head, kind: 'ok' }
    return { text: `${head}，失敗 ${failed.length} 個（${failed[0].error}）`, kind: 'warn' }
  }
  return { text: `${label}：已完成`, kind: 'ok' }
}

/** Follow a job continued from the 中斷的工作 dialog until it ends. */
export async function followJob(jobId, label) {
  try {
    for (;;) {
      await new Promise(r => setTimeout(r, 1000))
      const j = await patiently(() => api(`/api/jobs/${jobId}`))
      if (j.status === 'running') {
        const line = fmtJobProgress(j.progress)
        setBusy(line ? `${label}：${line}` : `${label}…`)
        continue
      }
      const s = jobSummary(label, j)
      setStatus(s.text, s.kind)
      return j
    }
  } catch (e) {
    setStatus(`${label}：${errText(e)}`, 'warn')
    return null
  } finally {
    setBusy('')
  }
}
