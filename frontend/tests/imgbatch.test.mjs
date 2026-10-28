// Image duplicate batch (src/imgbatch.js): what one 「執行建議」 deletes.
//
// Run:  node tests/imgbatch.test.mjs      (from frontend/)
import { batchPlan, liveDistance, batchRisk, BATCH_MAX } from '../src/imgbatch.js'

let fail = 0
function check(cond, msg) {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
let n = 0
const f = (dist, o = {}) => ({ path: `p${++n}`, size: 100, distance: dist, exact: n, ...o })
const grp = (files, o = {}) => ({ files, distance: Math.max(...files.map(x => x.distance)),
                                  keeper: files[0].path, certain: true, ...o })

const g0 = grp([f(0), f(0)])
const g10 = grp([f(0), f(10)])
const g18 = grp([f(0), f(18)])
const unsure = grp([f(0), f(3)], { keeper: null, certain: false })
const exactNoKeeper = grp([f(5, { exact: 99 }), f(5, { exact: 99 })], { keeper: null })
const gs = [g0, g10, g18, unsure, exactNoKeeper]

let p = batchPlan(gs, 0)
check(p.groups.length === 2 && p.groups.includes(g0) && p.groups.includes(exactNoKeeper),
      '門檻 0：距離 0 的組 + 位元組完全相同的組（不看距離）')
check(!p.paths.includes(g0.keeper) && p.paths.includes(g0.files[1].path),
      '刪的是建議保留以外的那張')
check(p.paths.includes(exactNoKeeper.files[1].path) && !p.paths.includes(exactNoKeeper.files[0].path),
      '完全相同但沒有建議：留第一張，絕不整組刪光')

p = batchPlan(gs, 14)
check(p.groups.includes(g10) && !p.groups.includes(g18), '門檻 14：含 10、不含 18')
check(p.undecided === 1 && !p.groups.includes(unsure), '無法判斷該留哪張的組不處理，另外計數')
check(p.bytes === p.paths.length * 100, '可釋出的容量 = 要刪的檔案大小總和')

p = batchPlan(gs, BATCH_MAX)
check(p.groups.includes(g18), `門檻 ${BATCH_MAX}：含 18`)

g10.files[1].deleted = true
check(liveDistance(g10) === 0 && batchPlan([g10], 20).groups.length === 0,
      '只剩一張的組已經處理完，不再列入')

const g2 = grp([f(0), f(12), f(4)])
g2.files[1].deleted = true
check(liveDistance(g2) === 4, '距離只算還在的檔案（刪掉最遠那張後，組變得更像）')

check(batchRisk(14).level === 'safe' && batchRisk(15).level === 'mid'
      && batchRisk(19).level === 'mid' && batchRisk(20).level === 'high', '風險說明的分界：14／19')

console.log(fail ? `\n${fail} 項失敗` : '\n全部通過')
process.exit(fail ? 1 : 0)
