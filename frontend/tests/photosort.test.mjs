/**
 * The profile grid's sort orders, 相似 above all.
 *
 * 相似 is the rule people will not check by reading code: a group has to land
 * where its newest member would have been, everything ungrouped has to keep
 * its time position, and flipping direction must not scramble the groups.
 */
import assert from 'node:assert/strict'
import { naturalCompare, sortMedia, nextSort, thresholdHint, colorHint } from '../src/photosort.js'

let ok = 0
const t = (what, fn) => {
  try { fn(); ok++; console.log('  ok   ' + what) }
  catch (e) { console.log('  FAIL ' + what + '\n       ' + e.message); process.exitCode = 1 }
}
const m = (name, mtime, size = 1, extra = {}) =>
  ({ path: 'G:\\X\\P\\' + name, mtime, size, is_video: false, ...extra })
const names = arr => arr.map(x => x.path.split('\\').pop())

t('名稱排序認數字：_21 在 _208 前面，_208 在 _2080 前面', () => {
  const got = ['R_2080.jpg', 'R_21.jpg', 'R_208.jpg', 'R_3.jpg'].sort(naturalCompare)
  assert.deepEqual(got, ['R_3.jpg', 'R_21.jpg', 'R_208.jpg', 'R_2080.jpg'])
})

t('名稱排序不分大小寫', () => {
  assert.equal(naturalCompare('abc.jpg', 'ABC.jpg'), 0)
})

t('最近新增：新的在前；大小：大的在前', () => {
  const items = [m('a', 1, 30), m('b', 3, 10), m('c', 2, 20)]
  assert.deepEqual(names(sortMedia(items, 'newest', 'desc')), ['b', 'c', 'a'])
  assert.deepEqual(names(sortMedia(items, 'size', 'desc')), ['a', 'c', 'b'])
  assert.deepEqual(names(sortMedia(items, 'size', 'asc')), ['b', 'c', 'a'])
})

t('相似：同組照片拉到組內最新那張的位置', () => {
  // 時間序（新到舊）：n5 g4 n3 g2 n1，g4 與 g2 同組
  const items = [m('n1', 1), m('g2', 2), m('n3', 3), m('g4', 4), m('n5', 5)]
  const groups = { [items[1].path]: 0, [items[3].path]: 0 }
  assert.deepEqual(names(sortMedia(items, 'similar', 'desc', groups)),
                   ['n5', 'g4', 'g2', 'n3', 'n1'])
})

t('相似：沒分組的照片完全維持時間位置', () => {
  const items = [m('a', 1), m('b', 2), m('c', 3)]
  assert.deepEqual(names(sortMedia(items, 'similar', 'desc', {})),
                   names(sortMedia(items, 'newest', 'desc')))
})

t('相似：反轉方向時，組錨定在最舊那張，組內也是舊到新', () => {
  const items = [m('n1', 1), m('g2', 2), m('n3', 3), m('g4', 4), m('n5', 5)]
  const groups = { [items[1].path]: 0, [items[3].path]: 0 }
  assert.deepEqual(names(sortMedia(items, 'similar', 'asc', groups)),
                   ['n1', 'g2', 'g4', 'n3', 'n5'])
})

t('相似：多組互不干擾，每張照片恰好出現一次', () => {
  const items = Array.from({ length: 8 }, (_, i) => m('p' + i, i))
  const groups = { [items[0].path]: 'A', [items[5].path]: 'A',
                   [items[2].path]: 'B', [items[7].path]: 'B' }
  const got = sortMedia(items, 'similar', 'desc', groups)
  assert.equal(got.length, 8)
  assert.equal(new Set(got.map(x => x.path)).size, 8)
  assert.deepEqual(names(got), ['p7', 'p2', 'p6', 'p5', 'p0', 'p4', 'p3', 'p1'])
})

t('相似：IG 影片沒有雜湊，維持時間位置', () => {
  const items = [m('g1', 1), m('v2', 2, 1, { is_video: true }), m('g3', 3)]
  const groups = { [items[0].path]: 0, [items[2].path]: 0 }
  assert.deepEqual(names(sortMedia(items, 'similar', 'desc', groups)), ['g3', 'g1', 'v2'])
})

t('點同一個排序鈕反轉方向，換欄位從自然方向開始', () => {
  assert.deepEqual(nextSort({ by: 'newest', dir: 'desc' }, 'newest'), { by: 'newest', dir: 'asc' })
  assert.deepEqual(nextSort({ by: 'newest', dir: 'desc' }, 'name'), { by: 'name', dir: 'asc' })
  assert.deepEqual(nextSort({ by: 'name', dir: 'asc' }, 'similar'), { by: 'similar', dir: 'desc' })
})

t('原本的陣列不被改動', () => {
  const items = [m('a', 1), m('b', 2)]
  sortMedia(items, 'newest', 'desc')
  assert.deepEqual(names(items), ['a', 'b'])
})

t('門檻說明對應實測的三段', () => {
  assert.equal(thresholdHint(50).level, 'safe')
  assert.equal(thresholdHint(70).level, 'ok')
  assert.equal(thresholdHint(85).level, 'risky')
})

t('顏色門檻說明對應實測的三段（整數百分比）', () => {
  assert.equal(colorHint(96).level, 'safe')
  assert.equal(colorHint(93).level, 'ok')
  assert.equal(colorHint(91).level, 'risky')
})

console.log(`\n${ok} 通過`)
