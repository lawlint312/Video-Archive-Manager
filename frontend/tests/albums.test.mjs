/**
 * The profile tab's album view (src/albums.js).
 *
 * The rules a glance at the page would not catch: the flat grid must be
 * exactly what it was, a character with one set must not get an album view,
 * and the pinned loose/IG albums must stay first whatever the sort.
 */
import assert from 'node:assert/strict'
import { flatItems, pickCover, buildAlbums, albumsApply, sortAlbums,
         nextAlbumSort, albumCaption, LOOSE_NAME } from '../src/albums.js'

let ok = 0
const t = (what, fn) => {
  try { fn(); ok++; console.log('  ok   ' + what) }
  catch (e) { console.log('  FAIL ' + what + '\n       ' + e.message); process.exitCode = 1 }
}
const f = (album, name, extra = {}) => ({
  path: `H:\\S\\Yeha\\${album ? album + '\\' : ''}${name}`,
  album, is_video: /\.mp4$/.test(name), in_set: false,
  mtime: 1, size: 10, ...extra,
})

const items = [
  f('', 'loose.jpg'),
  f('Vol.163', '002.jpg', { mtime: 5 }),
  f('Vol.163', '001.jpg'),
  f('Vol.163', 'clip.mp4', { in_set: true, size: 1000 }),
  f('Vol.26', '10.jpg'),
  f('Vol.26', '9.jpg', { mtime: 9 }),
  f('IG', 'p.jpg'),
  f('PPV', 'a.mp4', { in_set: true }),
]

t('平鋪檢視丟掉組裡的影片，其餘不動', () => {
  const flat = flatItems(items)
  assert.equal(flat.length, 6)
  assert.ok(flat.every(m => !m.in_set))
})

t('每個子資料夾一本，根目錄的散照片自成一本', () => {
  const a = buildAlbums(items)
  assert.deepEqual(a.map(x => x.key).sort(), ['', 'IG', 'PPV', 'Vol.163', 'Vol.26'])
  const loose = a.find(x => x.key === '')
  assert.equal(loose.kind, 'loose')
  assert.equal(loose.name, LOOSE_NAME)
  assert.equal(a.find(x => x.key === 'IG').kind, 'ig')
})

t('張數、部數、容量、最新時間', () => {
  const v = buildAlbums(items).find(x => x.key === 'Vol.163')
  assert.equal(v.photos, 2)
  assert.equal(v.videos, 1)
  assert.equal(v.size, 1020)
  assert.equal(v.mtime, 5)
  assert.equal(albumCaption(v), '2 張 · 1 部影片')
})

t('封面：依名稱的第一張照片（認數字：9 在 10 前面）', () => {
  assert.equal(pickCover(buildAlbums(items).find(x => x.key === 'Vol.26').items)
    .path.split('\\').pop(), '9.jpg')
  assert.equal(pickCover(buildAlbums(items).find(x => x.key === 'Vol.163').items)
    .path.split('\\').pop(), '001.jpg')
})

t('封面：有 poster / cover 就用它，影片不算', () => {
  const list = [f('A', '001.jpg'), f('A', 'cover.png'), f('A', 'poster.mp4')]
  assert.equal(pickCover(list).path.split('\\').pop(), 'cover.png')
})

t('封面：只有影片的組用影片', () => {
  assert.equal(pickCover([f('PPV', 'b.mp4'), f('PPV', 'a.mp4')])
    .path.split('\\').pop(), 'a.mp4')
  assert.equal(pickCover([]), null)
})

t('分類沒開 → 不用相簿', () => {
  assert.equal(albumsApply(false, buildAlbums(items)), false)
})

t('有照片的子資料夾 ≥ 2 才用相簿', () => {
  assert.equal(albumsApply(true, buildAlbums(items)), true)
  // one set + loose + a videos-only folder: that is the flat grid behind a click
  const one = [f('', 'a.jpg'), f('Vol.1', 'b.jpg'), f('PPV', 'c.mp4', { in_set: true })]
  assert.equal(albumsApply(true, buildAlbums(one)), false)
  assert.equal(albumsApply(true, []), false)
})

t('排序：名稱認數字，未分組與 IG 永遠在最前面', () => {
  const got = sortAlbums(buildAlbums(items), 'name', 'asc').map(x => x.key)
  assert.deepEqual(got, ['', 'IG', 'PPV', 'Vol.26', 'Vol.163'])
  const rev = sortAlbums(buildAlbums(items), 'name', 'desc').map(x => x.key)
  assert.deepEqual(rev, ['', 'IG', 'Vol.163', 'Vol.26', 'PPV'])
})

t('排序：最近新增 / 數量', () => {
  const byNew = sortAlbums(buildAlbums(items), 'newest', 'desc').map(x => x.key)
  assert.deepEqual(byNew.slice(2), ['Vol.26', 'Vol.163', 'PPV'])
  const byCount = sortAlbums(buildAlbums(items), 'count', 'desc').map(x => x.key)
  assert.deepEqual(byCount.slice(2), ['Vol.163', 'Vol.26', 'PPV'])
})

t('排序鈕：同一顆翻方向，換一顆從自然方向開始', () => {
  assert.deepEqual(nextAlbumSort({ by: 'name', dir: 'asc' }, 'name'), { by: 'name', dir: 'desc' })
  assert.deepEqual(nextAlbumSort({ by: 'name', dir: 'asc' }, 'count'), { by: 'count', dir: 'desc' })
})

console.log(process.exitCode ? '\nFAILED' : `\nall good (${ok})`)
