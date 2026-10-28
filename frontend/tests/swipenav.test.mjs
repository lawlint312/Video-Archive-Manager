/**
 * node tests/swipenav.test.mjs   (from frontend/)
 *
 * The swipe-to-change-photo thresholds. These exist as a module rather than
 * inline in the viewer because they cannot be checked by reading them — the
 * failure modes are "it missed my flick" and "it changed photo when I was
 * only holding still", neither of which is visible in the source.
 */
import { swipeOutcome, follow, fade, distanceFor,
         DEADZONE, VELOCITY, FLICK_MIN } from '../src/swipenav.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

console.log('\n方向：上滑是下一張')
check(swipeOutcome({ dy: -200, dt: 300 }) === 'next', '往上滑 → next')
check(swipeOutcome({ dy: 200, dt: 300 }) === 'prev', '往下滑 → prev')

console.log('\n距離不夠、速度也不夠的就不算')
check(swipeOutcome({ dy: -40, dt: 800 }) === 'cancel',
      '慢慢移動 40px 不算 —— 那多半是拿穩手機時的位移')
// raised after real use: the first version fired at 90px, about 11% of a
// phone screen, and simply moving your thumb while looking kept changing photo
check(swipeOutcome({ dy: -100, dt: 900, height: 812 }) === 'cancel',
      `慢慢移動 100px 也不算（812px 螢幕的門檻是 ${distanceFor(812).toFixed(0)}px）`)
check(swipeOutcome({ dy: -180, dt: 900, height: 812 }) === 'next',
      '但明確地滑 180px 就算')
check(swipeOutcome({ dy: -3, dt: 100 }) === 'cancel', `手指抖動（< ${DEADZONE}px）不算`)
check(swipeOutcome({ dy: 0, dt: 0 }) === 'cancel', '純點擊不算')

console.log('\n但快速輕彈要算，不然會覺得很鈍')
// Reported from real use: "手指往上一點點就會觸發換頁". The velocity test had
// no distance floor, so a 15px twitch at 1 px/ms qualified — and raising the
// distance threshold did nothing, because the flick path skipped it.
check(swipeOutcome({ dy: -15, dt: 15 }) === 'cancel',
      `快速輕撥 15px（1.00 px/ms）不算 —— 輕彈也要走過 ${FLICK_MIN}px`)
check(swipeOutcome({ dy: -30, dt: 25 }) === 'cancel', '快速輕撥 30px 也不算')
check(swipeOutcome({ dy: -60, dt: 60 }) === 'next',
      `60px 但很快（${(60 / 60).toFixed(2)} px/ms ≥ ${VELOCITY}）→ 算`)
check(swipeOutcome({ dy: -60, dt: 200 }) === 'cancel',
      `同樣 60px 但慢（${(60 / 200).toFixed(2)} px/ms）→ 不算`)
check(swipeOutcome({ dy: -distanceFor(800) - 1, dt: 3000 }) === 'next',
      '超過距離門檻就算，多慢都算')

console.log('\n斜向的手勢不能誤判')
check(swipeOutcome({ dx: 200, dy: -60, dt: 300 }) === 'cancel',
      '主要是水平移動 → 不切換（否則橫向平移會一直跳照片）')
check(swipeOutcome({ dx: 30, dy: -200, dt: 300 }) === 'next',
      '略帶傾斜但主要向上 → 仍然切換')
check(swipeOutcome({ dx: 180, dy: -200, dt: 300 }) === 'cancel',
      '斜 45 度左右的不算 —— 要求明確地垂直')

console.log('\n橫向：改成左右滑動（使用者的選擇）')
{
  const X = g => swipeOutcome({ ...g, axis: 'x', height: 844 })
  check(X({ dx: -250, dt: 300 }) === 'next', '往左滑 → next（像相簿翻到下一張）')
  check(X({ dx: 250, dt: 300 }) === 'prev', '往右滑 → prev')
  check(X({ dx: 20, dy: -250, dt: 300 }) === 'cancel',
        '橫向時上下滑不換張 —— 否則兩個方向都會觸發')
  check(X({ dx: -40, dt: 20 }) === 'cancel', '輕彈也要走過 FLICK_MIN，不能一碰就換')
  check(X({ dx: -80, dt: 60 }) === 'next', '夠快又夠遠的輕彈照樣算')
  check(swipeOutcome({ dy: -200, dt: 300 }) === 'next', '沒給 axis 仍是上下（直向不變）')
}

console.log('\n門檻隨螢幕高度縮放')
check(distanceFor(812) > distanceFor(400), '大螢幕要滑得多一點')
check(distanceFor(400) === 120, '小螢幕有下限，不會變得太靈敏')

console.log('\n跟手：小幅一比一，大幅逐漸拉不動')
check(follow(50, 800) === 50, '小幅完全跟手')
check(follow(-50, 800) === -50, '兩個方向都是')
{
  const big = follow(-2000, 800)
  check(Math.abs(big) < 800 && big < 0,
        `拖很遠也不會離開畫面：${big.toFixed(0)}px（保留了回彈的餘地）`)
  const a = Math.abs(follow(500, 800)), b = Math.abs(follow(900, 800))
  check(b > a, '仍然單調遞增，只是越來越沉')
}

console.log('\n淡出：移動越多越淡，但不會全黑')
check(fade(0, 800) === 1, '沒動的時候完全不透明')
check(fade(200, 800) < 1 && fade(200, 800) > 0.5, `移動一段後 ${fade(200, 800).toFixed(2)}`)
check(fade(5000, 800) >= 0.45, '拖到底仍看得見，不然像整張消失了')

console.log(fail ? `\nFAIL (${fail})` : '\nPASS')
process.exit(fail ? 1 : 0)
