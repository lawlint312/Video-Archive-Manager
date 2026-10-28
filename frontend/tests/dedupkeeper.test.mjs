// Video duplicate keep-suggestion ladder (src/dedup.js suggestKeeper).
//
// The rung order is the whole point: location → duration → resolution →
// .mp4 → 10%+ larger → older. `.mp4` sits before "older" since 2026-10-21 — a `.mov` from
// 2022 and an `.mp4` from 2023 that tie on everything else used to keep the
// .mov, although the file date only says when that copy arrived.
//
// Run:  node tests/dedupkeeper.test.mjs      (from frontend/)
import { register } from 'node:module'
register('./extloader.mjs', import.meta.url)

globalThis.localStorage ??= { getItem: () => null, setItem() {}, removeItem() {} }
const { suggestKeeper } = await import('../src/dedup.js')

let fail = 0
function check(cond, msg) {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
const f = (name, o = {}) => ({ path: 'X/' + name, name, label: '歸檔/A', duration: 5,
                               width: 480, height: 656, mtime: 1000, ...o })
const keep = files => suggestKeeper(files).keeper?.name ?? null

check(keep([f('a_257.mov', { mtime: 100 }), f('a_246.mp4', { mtime: 900 })]) === 'a_246.mp4',
      '其他全平手：.mp4 贏過較舊的 .mov')
check(keep([f('a.mov', { width: 1080, height: 1920 }), f('a.mp4')]) === 'a.mov',
      '解析度仍排在 .mp4 前面')
check(keep([f('a.mov', { duration: 60 }), f('a.mp4', { duration: 30 })]) === 'a.mov',
      '時長仍排在 .mp4 前面')
check(keep([f('new.mp4', { mtime: 900 }), f('old.mp4', { mtime: 100 }), f('x.mov', { mtime: 1 })])
      === 'old.mp4', '兩個 .mp4 之間再比較舊（.mov 雖然最舊也不算）')
check(keep([f('Denise_11.mp4', { size: 19.8e6, mtime: 100 }),
            f('ScreenRecording_1.mp4', { size: 36e6, mtime: 900 })]) === 'ScreenRecording_1.mp4',
      '同時長同解析度：大 10% 以上的贏過較舊的')
check(keep([f('old.mp4', { size: 100e6, mtime: 100 }),
            f('new.mp4', { size: 105e6, mtime: 900 })]) === 'old.mp4',
      '只大 5%：不算，照舊留較舊的')
check(keep([f('big.mp4', { size: 50e6, width: 640, height: 360 }),
            f('hd.mp4', { size: 10e6, width: 1920, height: 1080 })]) === 'hd.mp4',
      '解析度仍排在大小前面')
check(keep([f('x.mov', { size: 90e6 }), f('y.mp4', { size: 10e6 })]) === 'y.mp4',
      '.mp4 仍排在大小前面')
check(suggestKeeper([f('a.mkv'), f('b.mkv')]).certain === false, '完全分不出就不給建議')

console.log(fail ? `\n${fail} 項失敗` : '\n全部通過')
process.exit(fail ? 1 : 0)
