// Tier 3 (same footage, another number): which file to keep.
//   node tests/relkeeper.test.mjs
import { suggestRelKeeper } from '../src/relkeeper.js'

let failed = 0
const check = (ok, msg) => { console.log((ok ? '  ok   ' : '  FAIL ') + msg); if (!ok) failed++ }

const f = (path, role, w = 1920, h = 1080, extra = {}) =>
  ({ path, name: path, role, width: w, height: h, deleted: false, ...extra })
const firstOnly = files => ({ keeper: files[0] })     // stands in for the ladder

let r = suggestRelKeeper([f('UFD-061.mp4', 'parent'), f('AKO-599.mp4', 'child')], firstOnly)
check(r.keeper.path === 'UFD-061.mp4' && r.certain, '留原片')

r = suggestRelKeeper([f('UFD-061.mp4', 'parent', 1280, 720), f('AKO-599.mp4', 'child', 3840, 2160)],
                     firstOnly)
check(r.keeper.path === 'UFD-061.mp4' && !r.certain && r.why.includes('畫質'),
      '擷取版是 4K 重製、比原片清楚：建議照舊，但標成要人工判斷')

r = suggestRelKeeper([f('UFD-061.mp4', 'parent'), f('UFD-061.mkv', 'parent'),
                      f('AKO-599.mp4', 'child')], firstOnly)
check(r.keeper.path === 'UFD-061.mp4' && r.certain, '原片有兩份：交給一般的保留規則挑')
r = suggestRelKeeper([f('UFD-061.mp4', 'parent'), f('UFD-061.mkv', 'parent'),
                      f('AKO-599.mp4', 'child')], () => ({ keeper: null }))
check(r.keeper === null && !r.certain, '一般規則也挑不出來：不建議')

r = suggestRelKeeper([f('UFD-061.mp4', 'parent', 1920, 1080, { deleted: true }),
                      f('AKO-599.mp4', 'child'), f('AKO-385.mp4', 'child')], firstOnly)
check(r.keeper === null && r.certain && r.why === '原片已刪除',
      '原片被刪了：剩下的是不同的片，不是重複，不再建議刪誰')

r = suggestRelKeeper([f('UFD-061.mp4', 'parent'), f('AKO-599.mp4', 'child', 1920, 1080,
                                                     { deleted: true })], firstOnly)
check(r.keeper.path === 'UFD-061.mp4' && r.certain, '擷取版刪掉了：這組處理完')

console.log(failed ? `\n${failed} FAILED` : '\nPASS')
process.exit(failed ? 1 : 0)
