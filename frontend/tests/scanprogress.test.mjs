/**
 * node tests/scanprogress.test.mjs   (from frontend/)
 *
 * The formatting rules for scan progress. Two of these exist because the
 * obvious implementation is wrong in a way that only shows up on real data:
 * an unknown total must not become 0%, and a string progress from the other
 * job kinds must not become "[object Object]" in the shared status bar.
 */
import { pct, fmtEta, fmtProgressCount, fmtProgressLine }
  from '../src/scanprogress.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

console.log('\n百分比：不知道總數時不要畫進度條')
check(pct({ done: 50, total: 200 }) === 25, '一般情況')
check(pct({ done: 0, total: 200 }) === 0, '剛開始是 0%')
check(pct({ done: 200, total: 200 }) === 100, '跑完是 100%')
check(pct({ done: 412, total: null }) === null,
      '走訪階段沒有總數 → null（畫成 0% 看起來像卡住了）')
check(pct({ done: 5, total: 0 }) === null, 'total 是 0 也不畫，不要除以零')
check(pct(null) === null, '沒有進度資料時不爆掉')
check(pct({ done: 250, total: 200 }) === 100, '超出時夾在 100（寧可滿也不要爆表）')

console.log('\n剩餘時間：粗略但誠實')
check(fmtEta(3) === '快好了', '幾秒鐘就別報數字了')
check(fmtEta(42) === '約 40 秒', '一分鐘內取整到 5 秒')
check(fmtEta(240) === '約 4 分鐘', '幾分鐘')
check(fmtEta(3600) === '約 1 小時', '整點不要寫「1 小時 0 分」')
check(fmtEta(5400) === '約 1 小時 30 分', '小時加分鐘')
check(fmtEta(null) === '', '沒有估計值就不顯示，不要寫「計算中」佔位')
check(fmtEta(Infinity) === '', '速率為零時不顯示')
check(fmtEta(-5) === '', '負數不顯示')

console.log('\n計數')
check(fmtProgressCount({ done: 1204, total: 3318 }) === '1,204 / 3,318',
      '千分位 —— 16204 這種數字沒有逗號很難一眼讀')
check(fmtProgressCount({ done: 412, total: null }) === '已找到 412',
      '沒有總數時只報已找到多少')
check(fmtProgressCount({ done: 16500, total: 16204, approx: true })
      === '已找到 16,500',
      '估計值不能寫成分母 —— 檔案變多時會出現「16,500 / 16,204」')
check(pct({ done: 16500, total: 16204, approx: true }) === 100,
      '但進度條還是可以用估計值（夾在 100）')

console.log('\n狀態列的單行文字')
{
  const line = fmtProgressLine({
    phase: 3, phases: 4, label: '讀取時長',
    done: 1204, total: 3318, eta: 243,
  })
  check(line === '階段 3/4 · 讀取時長 · 1,204 / 3,318 · 約 4 分鐘', line)
}
check(fmtProgressLine('轉檔中 40%') === '轉檔中 40%',
      '其他工作仍然送字串，要原樣通過（不然狀態列會顯示 [object Object]）')
check(fmtProgressLine(null) === '', '空的就是空的')
{
  const line = fmtProgressLine({
    phase: 1, phases: 4, label: '走訪檔案', done: 412, total: null, eta: null,
  })
  check(line === '階段 1/4 · 走訪檔案 · 已找到 412',
        `沒有總數與估計值時不留空欄位：${line}`)
}

console.log('\n' + (fail ? `${fail} 項失敗` : '全部通過'))
process.exit(fail ? 1 : 0)
