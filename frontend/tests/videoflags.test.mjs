/**
 * node tests/videoflags.test.mjs   (from frontend/)
 *
 * When a video counts as watched, and what the listing filters keep.
 *
 * The gate is here rather than inline in the player for the usual reason: its
 * failure modes are invisible in the source. "It marked a video I only
 * mis-tapped" and "it never marked one I actually watched" both look like
 * perfectly reasonable code, and checking them by hand means sitting in front
 * of a browser with a stopwatch.
 */
import { createWatchGate, filterVideos, isWikiMode, WATCH_SECONDS, markIcons,
         favGuard, favGuardMessage }
  from '../src/videoflags.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

function gate() {
  let hits = 0
  const g = createWatchGate({ onReach: () => hits++ })
  return { g, hits: () => hits }
}

console.log('\n誤觸不算')
{
  const { g, hits } = gate()
  g.play(0)
  g.pause(400)          // opened and closed again in under half a second
  g.tick(400)
  check(hits() === 0, '開了 0.4 秒就關掉 —— 不算看過')
}

console.log('\n真的看了就算')
{
  const { g, hits } = gate()
  g.play(0)
  g.tick(4999)
  check(hits() === 0, `播到 ${WATCH_SECONDS} 秒之前都還不算`)
  g.tick(5000)
  check(hits() === 1, '滿 5 秒就記錄')
}

console.log('\n只送一次')
{
  const { g, hits } = gate()
  g.play(0)
  g.tick(6000)
  g.tick(9000)
  g.tick(30000)
  check(hits() === 1, '之後再怎麼播都不會重送 —— 存的是第一次的時間')
}

console.log('\n暫停不歸零（這是它必須是狀態機而不是 setTimeout 的原因）')
{
  const { g, hits } = gate()
  g.play(0)
  g.pause(4000)         // paused to read the memo
  g.tick(20000)         // a lot of wall-clock time passes while paused
  check(hits() === 0, '暫停期間的時間不算進去')
  g.play(20000)
  g.tick(21001)         // 4000 + 1001 = 5001 ms of actual playback
  check(hits() === 1, '接著再播 1 秒就補滿了 —— 先前的 4 秒沒有被丟掉')
}

console.log('\n暫停著不動永遠不會觸發')
{
  const { g, hits } = gate()
  g.play(0)
  g.pause(100)
  for (let t = 100; t < 60000; t += 1000) g.tick(t)
  check(hits() === 0, '開著但沒在播，放一分鐘也不算')
}

console.log('\n篩選')
const list = [
  { path: 'a', watched: true,  fav: true },
  { path: 'b', watched: true,  fav: false },
  { path: 'c', watched: false, fav: false },
  { path: 'd', watched: false, fav: true },
]
check(filterVideos(list, 'all').length === 4, '全部 → 4 部')
check(filterVideos(list, 'unwatched').map(v => v.path).join('') === 'cd',
      '未看過 → c、d')
check(filterVideos(list, 'fav').map(v => v.path).join('') === 'ad',
      '我的最愛 → a、d（看過與否無關）')

console.log('\n認不得的模式要顯示全部，不是顯示空的')
check(filterVideos(list, 'nonsense').length === 4,
      'localStorage 裡的舊值不該讓整個庫看起來是空的')

console.log('\n沒有標記欄位的清單（後端還沒重開）也要能用')
const old = [{ path: 'x' }, { path: 'y' }]
check(filterVideos(old, 'unwatched').length === 2, '沒有 watched 欄位 → 都算未看過')
check(filterVideos(old, 'fav').length === 0, '沒有 fav 欄位 → 一個最愛都沒有')


console.log('\n對照面板點數字之後的篩選')
// Every row is tagged by the server: which side of the wiki list it falls on,
// whether it shares a catalogue number with another file, and which section
// its number belongs to.
const lib = [
  { path: 'a', video_id: 'NSPS-694', wiki_state: 'have', wiki_dupe: false, wiki_section: '単体作品' },
  { path: 'b', video_id: 'ATID-480', wiki_state: 'have', wiki_dupe: true,  wiki_section: '配信作品' },
  { path: 'c', video_id: 'ATID-480', wiki_state: 'have', wiki_dupe: true,  wiki_section: '配信作品' },
  { path: 'd', video_id: 'ARA-348',  wiki_state: 'extra', wiki_dupe: false, wiki_section: '' },
  { path: 'e', video_id: '',         wiki_state: 'none',  wiki_dupe: false, wiki_section: '' },
]
const paths = m => filterVideos(lib, m).map(v => v.path).join('')
check(paths('wiki:extra') === 'd', 'wiki 未收錄 → 只有 d')
check(paths('wiki:none') === 'e', '認不出番號 → 只有 e')
check(paths('wiki:dupe') === 'bc',
      '重複 → 兩份都要出現，只給一份就沒得比較了')
check(paths('wiki:sec:単体作品') === 'a', '按區段篩選')
check(paths('wiki:sec:配信作品') === 'bc', '區段裡的重複檔照樣算')
check(paths('wiki:sec:VR作品') === '', '一部都沒有的區段就是空的')

console.log('\n模式互斥：一次只有一個篩選')
check(isWikiMode('wiki:extra') && isWikiMode('wiki:sec:VR作品'), '認得出是面板設的')
check(!isWikiMode('unwatched') && !isWikiMode('all') && !isWikiMode(null),
      '看過／最愛那組不是')
check(filterVideos(lib, 'wiki:nonsense').length === lib.length,
      '認不得的 wiki 模式一樣顯示全部，不是顯示空的')

console.log('\n沒有這些欄位的清單（後端還沒重開）不能全部消失')
const old2 = [{ path: 'x' }, { path: 'y' }]
check(filterVideos(old2, 'wiki:extra').length === 0, 'wiki 未收錄 → 0')
check(filterVideos(old2, 'wiki:dupe').length === 0, '重複 → 0')


console.log('\n發行日排序：沒有日期的排最後，兩個方向都是')
// Not "oldest": an unknown date is unknown. A blanket reverse would float
// them to the top when sorting newest-first, putting the least informative
// rows in front of the ones the sort was chosen to show.
const byRel = (list, dir) => {
  const s2 = [...list].sort((a, b) =>
    (a.released || '').localeCompare(b.released || ''))
  const out = dir === 'desc' ? s2.reverse() : s2
  return [...out.filter(v => v.released), ...out.filter(v => !v.released)]
    .map(v => v.path).join('')
}
const rel = [
  { path: 'a', released: '2018-04-13' },
  { path: 'b', released: '' },
  { path: 'c', released: '2015-07-04' },
]
check(byRel(rel, 'asc') === 'cab', '舊到新：c(2015) a(2018)，沒日期的 b 在最後')
check(byRel(rel, 'desc') === 'acb', '新到舊：a c，沒日期的 b 還是在最後')
check(byRel([{ path: 'x' }, { path: 'y' }], 'asc') === 'xy',
      '全部都沒有日期時不會爆，順序維持穩定')


console.log('卡片上的標記')
// A favourite implies you watched it, so the eye next to a heart says nothing
// the heart did not already say — and this corner is 40px wide.
{
  const keys = v => markIcons(v).map(m => m.key)
  check(keys({ watched: true, fav: false }).join() === 'seen',
        '只有看過 → 眼睛')
  check(keys({ watched: false, fav: true }).join() === 'fav',
        '只有最愛（沒播滿 5 秒就先標了）→ 愛心')
  check(keys({ watched: true, fav: true }).join() === 'fav',
        '兩者都有 → 只畫愛心，眼睛不重複說一次')
  check(keys({ watched: false, fav: false }).length === 0,
        '都沒有 → 什麼都不畫')
  check(markIcons(null).length === 0, 'null 不會爆')
  // the flags themselves are untouched; this only decides what is drawn
  const v = { watched: true, fav: true }
  markIcons(v)
  check(v.watched === true && v.fav === true, '不會去改資料本身')
}

console.log('\n刪除時的最愛防呆')
{
  check(favGuard({ fav: true, fav_others: 3 }, 'admin').kind === 'own',
        '自己標的優先：連管理員也要先自己取消')
  const g = favGuard({ fav: false, fav_others: 2 }, 'admin')
  check(g.kind === 'others' && g.count === 2 && g.canForce,
        '只有別人標的：管理員可以確認後刪')
  check(!favGuard({ fav: false, fav_others: 2 }, 'editor').canForce,
        '可編輯帳號不能強制')
  check(favGuard({ fav: false, fav_others: 0 }, 'editor').kind === 'none',
        '沒人標就沒有阻擋')
  check(favGuard({ fav: false }, 'admin').kind === 'none',
        '舊的清單沒有 fav_others 欄位也不會誤擋')
  check(favGuardMessage('A.mp4', g).includes('2 位'), '確認視窗寫出人數')
  check(!favGuardMessage('A.mp4', g).match(/[a-z]+@|帳號：/),
        '只有人數，沒有是誰')
  check(favGuardMessage('A.mp4', favGuard({ fav_others: 1 }, 'editor'))
          .includes('管理員'),
        '不能刪的人被告知該找誰')
}

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
