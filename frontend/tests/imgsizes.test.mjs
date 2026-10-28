// The home grid's avatar sizes: one render per frame, none for sizes known.
//   node tests/imgsizes.test.mjs
import { sizeBatcher } from '../src/imgsizes.js'

let failed = 0
const check = (ok, msg) => { console.log((ok ? '  ok   ' : '  FAIL ') + msg); if (!ok) failed++ }

// a hand-cranked frame clock, and a table the way HomeView keeps it
function rig() {
  const frames = []
  const state = { table: {}, applies: 0 }
  const report = sizeBatcher(() => state.table,
    batch => { state.applies++; state.table = { ...state.table, ...batch } },
    f => frames.push(f))
  const tick = () => { while (frames.length) frames.shift()() }
  return { state, report, tick }
}

{
  const { state, report, tick } = rig()
  // JAV on 2026-10-28: 201 avatars in a 228-card section, all cached, so
  // they finish loading together
  for (let i = 0; i < 201; i++) report(`p${i}`, 400, 600)
  check(state.applies === 0, '載入當下不更新，等到下一個畫面')
  tick()
  check(state.applies === 1, `201 張在同一個畫面載入：只更新 1 次（原本 201 次，每次重畫 228 張卡片）`)
  check(Object.keys(state.table).length === 201, '201 個尺寸都記下來了')

  // collapse and reopen: new <img> elements, every one fires load again
  for (let i = 0; i < 201; i++) report(`p${i}`, 400, 600)
  tick()
  check(state.applies === 1, '收合再展開：尺寸都已知，完全不更新')

  report('p3', 600, 400)
  tick()
  check(state.applies === 2 && state.table.p3.w === 600, '圖換了（尺寸不同）照樣更新')
}

{
  const { state, report, tick } = rig()
  report('a', 100, 100)
  tick()
  report('b', 100, 100)
  report('c', 100, 100)
  tick()
  check(state.applies === 2, '分散在不同畫面的，每個畫面各一次')
  report('d', 0, 0)
  tick()
  check(state.applies === 2 && !state.table.d, '尺寸 0（還沒解碼完）不算')
}

console.log(failed ? `\n${failed} FAILED` : '\nPASS')
process.exit(failed ? 1 : 0)
