/**
 * Adding, editing and removing clip segments.
 *
 * The edit path is the one that was missing: segments arriving from the
 * contact sheet are coarse by construction, so a list that can only play and
 * delete makes the second stage of that flow impossible.
 */
import assert from 'node:assert/strict'
import { upsert, drop, commitable } from '../src/clipsegments.js'

let ok = 0
const t = (what, fn) => {
  try { fn(); ok++; console.log('  ok   ' + what) }
  catch (e) { console.log('  FAIL ' + what + '\n       ' + e.message); process.exitCode = 1 }
}

const base = () => [
  { id: 1, start: 10, end: 20 },
  { id: 2, start: 900, end: 960 },
]

t('沒在編輯時是新增，接在後面', () => {
  const got = upsert(base(), null, 30, 40, 7)
  assert.equal(got.length, 3)
  assert.deepEqual(got[2], { id: 7, start: 30, end: 40 })
})

t('編輯時是就地取代，不會多一段', () => {
  const got = upsert(base(), 2, 880, 930, 99)
  assert.equal(got.length, 2, '長度不能變')
  assert.deepEqual(got[1], { id: 2, start: 880, end: 930 })
  assert.deepEqual(got[0], { id: 1, start: 10, end: 20 }, '別段不能被動到')
})

t('取代後順序不變，編號不會跳動', () => {
  const got = upsert(base(), 1, 500, 600, 99)
  assert.deepEqual(got.map(s => s.id), [1, 2])
})

t('原本的陣列不會被改掉', () => {
  const orig = base()
  upsert(orig, 2, 1, 2, 9)
  assert.deepEqual(orig[1], { id: 2, start: 900, end: 960 })
})

t('移除只拿掉那一段', () => {
  assert.deepEqual(drop(base(), 1).map(s => s.id), [2])
  assert.deepEqual(drop(base(), 77).map(s => s.id), [1, 2], '不存在的 id 不影響')
})

t('不足一秒不能送出，那是伺服器的下限', () => {
  assert.equal(commitable(10, 11), true)
  assert.equal(commitable(10, 10.9), false)
  assert.equal(commitable(10, 10), false)
  assert.equal(commitable(20, 10), false, '反過來也不行')
  assert.equal(commitable(null, 10), false)
  assert.equal(commitable(0, 30), true, '0:00 起算是合法的')
})

console.log(`\n${ok} 通過`)
