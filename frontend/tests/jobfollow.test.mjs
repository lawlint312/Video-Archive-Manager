// Following jobs through a restart of the service.
//
//     node tests/jobfollow.test.mjs        (from frontend/)
//
// A page polling a scan used to give up on the first failed request — and a
// restart is nothing but failed requests for a few seconds, then a 404 until
// the new process has taken the scan back. Retrying anything, though, would
// hide real errors for a minute and a half.

import { register } from 'node:module'
register('./extloader.mjs', import.meta.url)

globalThis.localStorage = {
  getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {},
}

const J = await import('../src/jobfollow.js')

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

// a clock that only moves when the retry sleeps
function clock() {
  let t = 0
  return { now: () => t, sleep: async ms => { t += ms } }
}

console.log('what counts as a restart')
check('no answer', J.looksLikeRestart({ status: 0 }), true)
check('proxy with nothing behind it', J.looksLikeRestart({ status: 502 }), true)
check('job not taken back yet', J.looksLikeRestart({ status: 404 }), true)
check('forbidden is a real answer', J.looksLikeRestart({ status: 403 }), false)
check('a server error is a real answer', J.looksLikeRestart({ status: 500 }), false)

console.log('\nretrying through a restart')
{
  const c = clock()
  const answers = [{ status: 0 }, { status: 502 }, { status: 404 }, 'ok']
  let calls = 0
  const got = await J.patiently(async () => {
    const a = answers[calls++]
    if (a !== 'ok') throw a
    return 'answer'
  }, c)
  check('gets the answer once the server is back', got, 'answer')
  check('after asking four times', calls, 4)
}
{
  const c = clock()
  let calls = 0
  let err = null
  try { await J.patiently(async () => { calls++; throw { status: 403 } }, c) }
  catch (e) { err = e }
  check('a real error is not retried', [calls, err?.status], [1, 403])
}
{
  const c = clock()
  let err = null
  try { await J.patiently(async () => { throw { status: 0 } }, c) }
  catch (e) { err = e }
  check('gives up after the window', [err?.status, c.now() >= J.RESTART_WINDOW_MS],
        [0, true])
}

console.log('\nprogress lines for the file jobs')
check('convert', J.fmtJobProgress({ index: 3, total: 10, name: 'a.ts', pct: 0.42 }),
      '3/10 · a.ts · 42%')
check('convert stage', J.fmtJobProgress({ index: 1, total: 2, name: 'b.ts', pct: 0, stage: '檢查聲音' }),
      '1/2 · b.ts · 檢查聲音')
check('archive bytes', J.fmtJobProgress({ done: 50, total: 200, speed: 1, name: 'c.mp4' }),
      'c.mp4 · 25%')
check('folder move', J.fmtJobProgress({ phase: 'move', name: 'd.mp4', index: 4, count: 9 }),
      '4/9 · d.mp4')
check('plain string', J.fmtJobProgress('(2/5) 森田花梨'), '(2/5) 森田花梨')
check('scan progress', J.fmtJobProgress({ phase: 2, phases: 2, label: '比對', done: 1, total: 4 }),
      '階段 2/2 · 比對 · 1 / 4')

console.log('\nthe line once it ends')
check('converted', J.jobSummary('轉檔', { status: 'done', result: { converted: [1, 2], failed: [] } }),
      { text: '轉檔：完成 2 個', kind: 'ok' })
check('with failures', J.jobSummary('壓縮', { status: 'done',
      result: { done: [1], failed: [{ path: 'x', error: '無法讀取影片資訊' }] } }),
      { text: '壓縮：完成 1 個，失敗 1 個（無法讀取影片資訊）', kind: 'warn' })
check('one-shot job', J.jobSummary('歸檔 a.mp4', { status: 'done', result: { dst: 'y' } }),
      { text: '歸檔 a.mp4：已完成', kind: 'ok' })
check('error', J.jobSummary('剪輯', { status: 'error', error: '空間不足' }),
      { text: '剪輯失敗：空間不足', kind: 'warn' })

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
