/**
 * node tests/avatar.test.mjs   (from frontend/)
 *
 * Avatar framing — the maths shared by the profile header, the crop editor and
 * the home grid.
 *
 * This file exists because the home grid gained rectangular cards and the
 * maths was written for a circle: `coverPercent` decided which edge fits by
 * comparing the image's aspect ratio against 1. That is only right for a square
 * box. Two things have to hold:
 *
 *   - with `boxAr = 1` the answers must be byte-identical to before, because
 *     the profile header and the crop editor still pass nothing; and
 *   - for any box, the laid-out image must actually COVER it — no gap — while
 *     keeping the focal point in view. A shortfall here is a sliver of card
 *     background down one edge, which looks like a rendering glitch rather
 *     than a bug in a formula.
 */
import { coverPercent, clampFocal, avatarImgStyle, coverSizePx }
  from '../src/avatar.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
const near = (a, b, eps = 1e-9) => Math.abs(a - b) < eps

console.log('\n正方形框：跟加參數之前完全一樣')
// The old implementation, verbatim, as the reference.
function oldCover(natW, natH, zoom = 1) {
  const z = Math.max(1, zoom || 1)
  if (!natW || !natH) return { w: 100 * z, h: 100 * z }
  const ar = natW / natH
  return ar >= 1 ? { w: 100 * ar * z, h: 100 * z }
                 : { w: 100 * z, h: (100 / ar) * z }
}
const SHAPES = [[1920, 1080], [1080, 1920], [800, 800], [3024, 4032],
                [4032, 3024], [1, 3], [3, 1]]
let same = true
for (const [w, h] of SHAPES) {
  for (const z of [1, 1.5, 2.75, 5]) {
    const a = oldCover(w, h, z), b = coverPercent(w, h, z)
    if (!near(a.w, b.w) || !near(a.h, b.h)) same = false
  }
}
check(same, `${SHAPES.length} 種圖片 x 4 種縮放，預設參數的答案沒有改變`)
check(near(coverPercent(0, 0, 2).w, 200), '沒有尺寸時退回 100% x zoom（不變）')

console.log('\n任何框比例都要真的蓋滿')
// The whole point: no strip of background showing down an edge.
const BOXES = [1, 16 / 9, 4 / 5, 9 / 16]
let covers = true
const misses = []
for (const box of BOXES) {
  for (const [w, h] of SHAPES) {
    const c = coverPercent(w, h, 1, box)
    if (c.w < 100 - 1e-9 || c.h < 100 - 1e-9) {
      covers = false
      misses.push(`${w}x${h} 在 ${box.toFixed(2)} → ${c.w.toFixed(1)}x${c.h.toFixed(1)}`)
    }
  }
}
check(covers, `4 種框 x ${SHAPES.length} 種圖片，寬高都 >= 100%`
              + (misses.length ? `（缺口：${misses[0]}）` : ''))

console.log('\n而且只蓋剛好，不會無謂放大')
// Exactly one of the two dimensions should sit at 100% at zoom 1 — if both
// overshoot, the picture is being enlarged for nothing.
let tight = true
for (const box of BOXES) {
  for (const [w, h] of SHAPES) {
    const c = coverPercent(w, h, 1, box)
    if (!(near(c.w, 100, 1e-6) || near(c.h, 100, 1e-6))) tight = false
  }
}
check(tight, '每一組都恰有一邊剛好貼齊 100%')

console.log('\n寬圖在寬框、直圖在直框')
// 16:9 image in a 16:9 box needs no spill at all.
const exact = coverPercent(1920, 1080, 1, 16 / 9)
check(near(exact.w, 100, 1e-6) && near(exact.h, 100, 1e-6),
      '比例完全相同時兩邊都是 100%，一點都不裁切')
// A 9:16 phone video poster in a 16:9 box has to spill vertically, a lot.
const tall = coverPercent(1080, 1920, 1, 16 / 9)
check(tall.h > 300, `9:16 的圖放進 16:9 的框要往上下溢出 ${tall.h.toFixed(0)}%`)
const wide = coverPercent(1920, 1080, 1, 9 / 16)
check(wide.w > 300, `16:9 的圖放進 9:16 的框要往左右溢出 ${wide.w.toFixed(0)}%`)

console.log('\n焦點被夾在「還蓋得住整個框」的範圍內')
for (const box of BOXES) {
  // ask for the far corner; it must be pulled back
  const c = clampFocal(1920, 1080, { x: 0, y: 0, zoom: 1 }, box)
  const { w, h } = coverPercent(1920, 1080, c.zoom, box)
  const left = 50 - (c.x / 100) * w
  const top = 50 - (c.y / 100) * h
  check(left <= 1e-9 && left >= 100 - w - 1e-9 &&
        top <= 1e-9 && top >= 100 - h - 1e-9,
        `框 ${box.toFixed(2)}：焦點拉到角落也不會露出背景`)
}
check(clampFocal(100, 100, { zoom: 99 }).zoom === 5, '縮放上限 5')
check(clampFocal(100, 100, { zoom: 0.1 }).zoom === 1, '縮放下限 1')
check(clampFocal(100, 100, null).x === 50, '沒有存過位置就置中')

console.log('\n產出的行內樣式')
const st = avatarImgStyle(1080, 1920, { x: 50, y: 30, zoom: 1 }, 16 / 9)
check(st.position === 'absolute', '絕對定位')
check(st.width.endsWith('%') && st.height.endsWith('%'), '用百分比 —— 任何尺寸都一樣')
check(parseFloat(st.height) > 100, '直圖在寬框裡高度要溢出')
check(parseFloat(st.top) <= 0, '往上偏移，不會在頂端留空隙')
const none = avatarImgStyle(0, 0, {})
check(none.objectFit === 'cover', '還不知道原始尺寸時先用 cover 撐著')

console.log('\ncoverSizePx 刻意還是只認正方形')
// Its only caller is the crop editor's drag handler, and that box is a circle.
// Giving it a boxAr nobody passes would be API surface with no user — but the
// assumption should be written down, because the next rectangular box added
// here is where it would bite.
const px = coverSizePx(200, 1920, 1080, 1)
check(near(px.h, 200), '正方形框：高度剛好貼齊')
check(near(px.w, 200 * 16 / 9), '寬度依原比例溢出')

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
