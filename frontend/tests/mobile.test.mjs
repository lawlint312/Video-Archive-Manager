/**
 * node tests/mobile.test.mjs   (from frontend/)
 *
 * CSS cannot import a constant, so the "is this a phone" condition is written
 * out in every `@media` block by hand, and once more in src/mobile.js for the
 * code that needs it (swipe gestures, the bare video player). If one of them
 * drifts, that one piece of the page quietly goes back to the desktop layout
 * on a phone held sideways — which is exactly the bug this condition fixed.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MOBILE_MQ, LANDSCAPE_MQ } from '../src/mobile.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

function walk(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|css)$/.test(n)) out.push(p)
  }
  return out
}

const files = walk(new URL('../src', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'))
let phone = 0, side = 0
console.log('\n每個手機用的 @media 都跟 mobile.js 一字不差')
for (const f of files) {
  const lines = readFileSync(f, 'utf8').split(/\r?\n/)
  lines.forEach((ln, i) => {
    const m = ln.match(/@media\s+(.*?)\s*\{/)
    if (!m) return
    const q = m[1]
    const where = `${f.split(/[\\/]src[\\/]/).pop()}:${i + 1}`
    if (/760px/.test(q)) {
      phone++
      check(q === MOBILE_MQ, `${where}  ${q === MOBILE_MQ ? '' : '→ ' + q}`)
    } else if (/orientation:\s*landscape/.test(q)) {
      side++
      check(q === LANDSCAPE_MQ, `${where}  ${q === LANDSCAPE_MQ ? '' : '→ ' + q}`)
    }
  })
}
check(phone >= 10, `找到 ${phone} 個手機 @media（少於 10 代表掃描壞了，不是全對）`)
check(side >= 1, `找到 ${side} 個橫向 @media`)

console.log('\n條件本身')
check(MOBILE_MQ.includes('(pointer: coarse) and (max-height: 500px)'),
      '觸控而且矮 = 手機（橫拿的手機寬 844–932px，只看寬度會落到電腦版）')
check(!/pointer: fine/.test(MOBILE_MQ), '滑鼠的電腦一條規則都不受影響')

console.log(fail ? `\nFAIL (${fail})` : '\nPASS')
process.exit(fail ? 1 : 0)
