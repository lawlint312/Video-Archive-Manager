// The contact-sheet progress bar's arithmetic.
//
//     node tests/sheetprogress.test.mjs        (from frontend/)
//
// The two risks are both about `ready` not behaving like a counter: the sheet
// cache has a size limit so it can shrink, and the sheet currently building is
// briefly described by both `ready` and `building`.
import {
  doneSheets, pct, isComplete, label, createEta,
} from '../src/sheetprogress.js'

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}
const near = (name, got, want, tol = 0.01) => {
  const ok = got !== null && Math.abs(got - want) <= tol
  if (ok) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${got}\n    expected ~${want}`); failed++ }
}

console.log('nothing to draw yet')
check('no status', pct(null), null)
check('duration unknown', pct({ total: 0, ready: 0 }), null)

console.log('\nwhole sheets')
check('nothing built', pct({ total: 20, ready: 0, building: null }), 0)
check('half', pct({ total: 20, ready: 10, building: null }), 50)
check('all', pct({ total: 20, ready: 20, building: null }), 100)

console.log('\nthe sheet being built moves the bar between whole sheets')
// without this the bar sits still for ~2 s then jumps 5%
near('8 done + 6 of 12 frames',
     pct({ total: 20, ready: 8, building: { index: 8, done: 6, of: 12 } }), 42.5)
near('8 done + 0 frames',
     pct({ total: 20, ready: 8, building: { index: 8, done: 0, of: 12 } }), 40)

console.log('\nthe in-flight sheet must not be counted twice')
// after the last frame lands, the file appears and `ready` includes it while
// `building` still names it — 9/20 is right, 9.75/20 is not
check('finished sheet already in ready',
      doneSheets({ total: 20, ready: 9, building: { index: 8, done: 9, of: 12 } }), 9)
check('building an index behind ready',
      doneSheets({ total: 20, ready: 12, building: { index: 3, done: 6, of: 12 } }), 12)

console.log('\nclamped against nonsense from the server')
check('ready above total', pct({ total: 20, ready: 25, building: null }), 100)
check('negative ready', pct({ total: 20, ready: -3, building: null }), 0)
check('building.of zero',
      doneSheets({ total: 20, ready: 5, building: { index: 5, done: 3, of: 0 } }), 5)

console.log('\ncompletion and label')
check('not complete', isComplete({ total: 20, ready: 19 }), false)
check('complete', isComplete({ total: 20, ready: 20 }), true)
check('label counts', label({ total: 20, ready: 8 }), '已產生 8 / 20 張')
// a 6-minute clip needs one sheet; "已產生 1 / 1 張" is noise
check('single sheet, done', label({ total: 1, ready: 1 }), '已產生')
check('single sheet, not yet', label({ total: 1, ready: 0 }), '產生中…')

console.log('\neta comes from the change since the bar opened')
{
  // opened on a video that is already 10/20 cached, then builds 1 sheet/sec
  const eta = createEta()
  const S = n => ({ total: 20, ready: n, building: null })
  check('needs a few samples first', eta.push(S(10), 1000), null)
  check('still warming up', eta.push(S(11), 2000), null)
  // 2 sheets in 2 s -> 1/s, 8 remaining
  near('after three samples', eta.push(S(12), 3000), 8, 0.2)
  // The instant 10 must not count as work done: at 12 sheets in 3 s the naive
  // rate would be 4/s and the estimate 2 s.
  const naive = (20 - 12) / (12 / 3)
  check('naive rate would have said something much smaller', naive < 4, true)
}

console.log('\neviction resets rather than reporting nonsense')
{
  const eta = createEta()
  const S = n => ({ total: 20, ready: n, building: null })
  eta.push(S(10), 1000); eta.push(S(11), 2000); eta.push(S(12), 3000)
  check('cache evicted three sheets', eta.push(S(9), 4000), null)
  eta.push(S(10), 5000)
  const v = eta.push(S(11), 6000)
  check('recovers once there are samples again', v !== null, true)
}

console.log('\nno movement yet is not an estimate of zero')
{
  const eta = createEta()
  const S = { total: 20, ready: 4, building: null }
  eta.push(S, 1000); eta.push(S, 2000)
  check('stalled', eta.push(S, 3000), null)
}

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
