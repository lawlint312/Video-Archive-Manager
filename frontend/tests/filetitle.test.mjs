// The rule that decides how far a title edit reaches.
import assert from 'node:assert/strict'
import { titleTargets } from '../src/filetitle.js'

let pass = 0, fail = 0
function t(name, fn) {
  try { fn(); pass++; console.log('ok   ' + name) }
  catch (e) { fail++; console.log('FAIL ' + name); console.log('  ' + e.message) }
}

// A realistic mix: two copies of one numbered film, plus three files with no
// catalogue number at all — which is what 95% of the library looks like.
const rows = () => [
  { path: 'D:\\a\\ABC-001.mp4', video_id: 'ABC-001', title: '' },
  { path: 'E:\\b\\abc001.mp4',  video_id: 'ABC-001', title: '' },
  { path: 'D:\\x\\clip1.mp4',   video_id: '',        title: '' },
  { path: 'D:\\y\\clip2.mp4',   video_id: '',        title: '' },
  { path: 'D:\\z\\clip3.mp4',   video_id: '',        title: '' },
]

t('番號涵蓋所有副本', () => {
  const hit = titleTargets(rows(), { video_id: 'ABC-001', path: '', title: 'T' })
  assert.equal(hit.length, 2)
  assert.deepEqual(hit.map(v => v.path).sort(),
                   ['D:\\a\\ABC-001.mp4', 'E:\\b\\abc001.mp4'])
})

t('路徑只涵蓋那一個檔案', () => {
  const hit = titleTargets(rows(), { video_id: '', path: 'D:\\y\\clip2.mp4', title: 'T' })
  assert.equal(hit.length, 1)
  assert.equal(hit[0].path, 'D:\\y\\clip2.mp4')
})

t('空番號不可以互相配對', () => {
  // The destructive case: every un-numbered video has video_id ''. Matching on
  // it would stamp one title across all three clips at once.
  const hit = titleTargets(rows(), { video_id: '', path: 'D:\\x\\clip1.mp4', title: 'T' })
  assert.equal(hit.length, 1, '沒番號的影片彼此不該被視為同一部')
})

t('清空片名走的是同一條規則', () => {
  const hit = titleTargets(rows(), { video_id: '', path: 'D:\\z\\clip3.mp4', title: '' })
  assert.equal(hit.length, 1)
})

t('配不到就不動任何一列', () => {
  assert.equal(titleTargets(rows(), { video_id: '', path: 'D:\\nope.mp4' }).length, 0)
  assert.equal(titleTargets(rows(), { video_id: 'ZZZ-999', path: '' }).length, 0)
})

t('清單是空的或未定義也不會炸', () => {
  assert.equal(titleTargets([], { video_id: '', path: 'x' }).length, 0)
  assert.equal(titleTargets(undefined, { video_id: '', path: 'x' }).length, 0)
})

console.log(`\n${pass} 通過 / ${fail} 失敗`)
process.exit(fail ? 1 : 0)
