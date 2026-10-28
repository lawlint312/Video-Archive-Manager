/**
 * Click -> cell -> timestamp, the arithmetic behind picking clip points off a
 * contact sheet.
 *
 * Worth its own file because the numbers are the whole feature: a cell mapped
 * one column across is a clip that starts 30 seconds early, and nothing about
 * that looks like an error. `mount.test.mjs` would render the viewer happily
 * with every one of these wrong.
 */
import assert from 'node:assert/strict'
import { cellAt, cellTime, framesOn, canAdd, sortSegments }
  from '../src/sheetcells.js'

let ok = 0
const t = (what, fn) => {
  try { fn(); ok++; console.log('  ok   ' + what) }
  catch (e) { console.log('  FAIL ' + what + '\n       ' + e.message); process.exitCode = 1 }
}

// The real layout: 4x3 tiles, 4px gutter, 480px tiles on a 16:9 source.
const TW = 480, TH = 270, PAD = 4, COLS = 4, ROWS = 3
const grid = { cols: COLS, rows: ROWS, pad: PAD,
               width: COLS * TW + (COLS + 1) * PAD,
               height: ROWS * TH + (ROWS + 1) * PAD }

// centre of cell (row, col) as a fraction of the image
const centre = (row, col) => ({
  fx: (PAD + col * (TW + PAD) + TW / 2) / grid.width,
  fy: (PAD + row * (TH + PAD) + TH / 2) / grid.height,
})

t('每一格的中心都對到自己', () => {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const { fx, fy } = centre(r, c)
      assert.equal(cellAt(fx, fy, grid), r * COLS + c, `第 ${r},${c} 格`)
    }
  }
})

t('格與格之間的縫隙不算任何一格', () => {
  const fx = (PAD + TW + PAD / 2) / grid.width          // 第一、二欄之間
  const { fy } = centre(0, 0)
  assert.equal(cellAt(fx, fy, grid), null)
})

t('最外圈的邊框也不算', () => {
  assert.equal(cellAt(1 / grid.width, 0.5, grid), null)
  assert.equal(cellAt(0.5, 1 / grid.height, grid), null)
})

t('範圍外回 null 而不是最接近的一格', () => {
  assert.equal(cellAt(-0.01, 0.5, grid), null)
  assert.equal(cellAt(0.5, 1.4, grid), null)
})

t('格子邊界剛好在正確的一側', () => {
  const { fy } = centre(0, 0)
  const justInside = (PAD + TW - 0.5) / grid.width
  const justOutside = (PAD + TW + 0.5) / grid.width
  assert.equal(cellAt(justInside, fy, grid), 0)
  assert.equal(cellAt(justOutside, fy, grid), null)
})

t('使用者給的例子：間隔 30 秒，第 8 格是 00:03:30', () => {
  // 第 8 格 = 索引 7，而第一格是 0:00
  assert.equal(cellTime(0, 7, 12, 30), 210)
  assert.equal(cellTime(0, 0, 12, 30), 0)
})

t('翻頁之後時間接得上，不會從零重來', () => {
  assert.equal(cellTime(1, 0, 12, 30), 360)       // 第二張第一格 = 6:00
  assert.equal(cellTime(2, 5, 12, 30), 870)
})

t('最後一張沒填滿的格子要算得出來', () => {
  // 總格數由伺服器給（/api/sheet/info 的 frames）；124 格 = 10 張滿的又多 4 格
  assert.equal(framesOn(0, 12, 124), 12)
  assert.equal(framesOn(10, 12, 124), 4)
  assert.equal(framesOn(11, 12, 124), 0)
  // 72 格剛好 6 張，最後一張是滿的 —— 邊界不能算成空的，也不能多出第 7 張
  assert.equal(framesOn(5, 12, 72), 12)
  assert.equal(framesOn(6, 12, 72), 0)
  assert.equal(framesOn(0, 12, 0), 0)
  assert.equal(framesOn(0, 12, undefined), 0)
})

t('終點必須在起點之後，同一格不能成段', () => {
  assert.equal(canAdd(210, 240), true)
  assert.equal(canAdd(210, 210), false, '同一格是零長度')
  assert.equal(canAdd(240, 210), false, '反過來選也不行')
  assert.equal(canAdd(null, 240), false)
  assert.equal(canAdd(0, 30), true, '從 0:00 開始是合法的，不是「沒選」')
})

t('片段依開始時間排序', () => {
  const got = sortSegments([{ start: 90, end: 120 }, { start: 30, end: 60 }])
  assert.deepEqual(got.map(s => s.start), [30, 90])
})


const { containBox, cellBox } = await import('../src/sheetcells.js')

t('object-fit: contain 的留白算得出來', () => {
  // 一張寬扁的圖放進偏方的框：上下留白
  const b = containBox(1000, 1000, 1000, 500)
  assert.equal(b.w, 1000); assert.equal(b.h, 500)
  assert.equal(b.x, 0);    assert.equal(b.y, 250)
  // 反過來：左右留白
  const c = containBox(1000, 1000, 500, 1000)
  assert.equal(c.x, 250);  assert.equal(c.y, 0)
})

t('放大後元素貼齊圖片，沒有留白', () => {
  const b = containBox(1936, 826, 1936, 826)
  assert.equal(b.x, 0); assert.equal(b.y, 0); assert.equal(b.scale, 1)
})

t('尺寸還沒量到時回 null，不是 NaN', () => {
  assert.equal(containBox(0, 0, 100, 100), null)
  assert.equal(containBox(100, 100, 0, 50), null)
})

t('標記框對得上該格，且跟點擊互為反函式', () => {
  for (const cell of [0, 3, 4, 11]) {
    const b = cellBox(cell, grid)
    const fx = (b.x + b.w / 2) / grid.width
    const fy = (b.y + b.h / 2) / grid.height
    assert.equal(cellAt(fx, fy, grid), cell, `第 ${cell} 格`)
  }
  assert.equal(cellBox(12, grid), null)
  assert.equal(cellBox(null, grid), null)
})

console.log(`\n${ok} 通過`)
