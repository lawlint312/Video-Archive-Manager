/**
 * node tests/syncplay.test.mjs   (from frontend/)
 *
 * Guards the two rules that made synchronised preview playback usable, both
 * of which look like arbitrary constants until you have watched it stutter.
 */
import { echoGuard, driftAction, DRIFT, RESYNC, NUDGE, GUARD_MS }
  from '../src/syncplay.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

console.log('\n回音抑制用的是「期限」，不是下一個 tick')
{
  let t = 0
  const g = echoGuard(() => t, 500)
  const el = {}
  g.hold(el)
  check(g.held(el), '剛設定就是被抑制的')
  t = 1                       // a next-tick flag would already be clear here
  check(g.held(el), '1 毫秒後仍然抑制（舊寫法就是在這裡漏掉的）')
  t = 60                      // a seek typically completes around here
  check(g.held(el), 'seeked 真正觸發的時間點還在抑制範圍內')
  t = 501
  check(!g.held(el), '過了期限就放行')
  check(!g.held({}), '沒被 hold 過的元素不受影響')
}

console.log('\n小幅漂移只調速度，絕不跳轉')
{
  const big = driftAction(RESYNC + 0.5)
  check(big.kind === 'seek', `差距 ${RESYNC + 0.5}s 才允許跳轉`)

  for (const d of [0.1, 0.3, 0.9, 1.4, -0.5]) {
    check(driftAction(d).kind !== 'seek',
          `差距 ${d}s 不跳轉（跳轉會讓解碼器卡住，反而製造更多漂移）`)
  }

  const behind = driftAction(0.2, 1)
  check(behind.kind === 'rate' && behind.rate > 1,
        `落後就加速：${behind.rate}`)
  const ahead = driftAction(-0.2, 1)
  check(ahead.kind === 'rate' && ahead.rate < 1, `超前就減速：${ahead.rate}`)
  check(Math.abs(behind.rate - 1) <= NUDGE + 1e-9,
        `調整幅度不超過 ${NUDGE * 100}%（再多就聽得出來）`)
}

console.log('\n要有死區，否則速度永遠不會安定')
{
  check(driftAction(DRIFT / 4, 1).rate === 1, '很接近時回到原速')
  check(driftAction(DRIFT * 0.75).kind === 'none',
        '在死區內不動作 —— 每一幀都修正的話速度永遠在變，聲音會聽得出來')
}

console.log('\n跟著使用者選的播放速度走')
{
  const r = driftAction(0.2, 2)
  check(r.rate > 2, `原速 2 倍時仍以 2 倍為基準（得到 ${r.rate}）`)
  check(driftAction(0, 1.5).rate === 1.5, '沒有漂移時就是使用者選的速度')
}

console.log('\n' + (fail ? `${fail} 項失敗` : '全部通過'))
process.exit(fail ? 1 : 0)
