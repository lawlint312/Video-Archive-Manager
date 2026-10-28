// The convert confirmation text — which files get deleted, replaced, or
// degraded. Also the guard that would have caught "開始轉檔 沒反應": that was
// a ReferenceError inside the click handler, invisible to every other test
// because none of them calls one.
import assert from 'node:assert/strict'
import { convertWarning } from '../src/convertwarn.js'

let pass = 0, fail = 0
function t(name, fn) {
  try { fn(); pass++; console.log('ok   ' + name) }
  catch (e) { fail++; console.log('FAIL ' + name); console.log('  ' + e.message) }
}

const remux   = { action: 'remux',    in_place: false }
const reenc   = { action: 'reencode', in_place: false }
const reencIP = { action: 'reencode', in_place: true }
const repair  = { action: 'repair',   in_place: false }
const retime  = { action: 'retime',   in_place: true }
const afix    = { action: 'audiofix', in_place: true }
const gfix    = { action: 'gapfix',   in_place: true }
const gcut    = { action: 'gapcut',   in_place: true }

t('每一種動作都算得出來，而且不會丟例外', () => {
  // The whole point: calling it at all is what was missing.
  for (const i of [remux, reenc, reencIP, repair, retime, afix, gfix, gcut]) {
    const m = convertWarning([i])
    assert.ok(m.includes('處理 1 個檔案'), i.action)
  }
})

t('只有換殼時不出現任何警告', () => {
  const m = convertWarning([remux, remux])
  assert.ok(m.includes('處理 2 個檔案'))
  assert.ok(!m.includes('⚠'))
  assert.ok(!m.includes('修正時序'))
})

t('重編碼要警告有損', () => {
  assert.ok(convertWarning([reenc]).includes('畫質會有損失'))
})

t('修正時序不可以被算進「就地取代」那句', () => {
  // That sentence says browsers cannot play these files and that the result is
  // lossy. Both are false for a retime, and it is `in_place` too — so this is
  // the assertion that keeps the two apart.
  const m = convertWarning([retime])
  assert.ok(!m.includes('瀏覽器播不動'), '時序修正被誤算進重編碼的警語')
  assert.ok(!m.includes('畫質會有損失'))
  assert.ok(m.includes('修正時序的 1 個'))
  assert.ok(m.includes('畫質不變'))
})

t('修復聲音不算進「瀏覽器播不動、有損」那句，另外說明', () => {
  const m = convertWarning([afix])
  assert.ok(!m.includes('瀏覽器播不動'), m)
  assert.ok(!m.includes('畫質會有損失'), m)
  assert.ok(m.includes('修復聲音的 1 個') && m.includes('畫質不變'), m)
})

t('修復斷訊也不算進「瀏覽器播不動、有損」那句，另外說明', () => {
  const m = convertWarning([gfix])
  assert.ok(!m.includes('瀏覽器播不動'), m)
  assert.ok(!m.includes('畫質會有損失'), m)
  assert.ok(m.includes('修復斷訊的 1 個') && m.includes('完整解碼驗證'), m)
})

t('就地重編碼仍然要警告', () => {
  const m = convertWarning([reencIP])
  assert.ok(m.includes('瀏覽器播不動'))
  assert.ok(m.includes('直接取代原檔'))
})

t('兩種就地動作混在一起時分別計數', () => {
  const m = convertWarning([reencIP, reencIP, retime])
  assert.ok(m.includes('其中 2 個本來就是 .mp4'), m)
  assert.ok(m.includes('修正時序的 1 個'), m)
})

t('修復要講明不刪原檔', () => {
  const m = convertWarning([repair])
  assert.ok(m.includes('「不會」刪除原始檔'))
})

t('去除空白：講明不重新編碼，不跟「瀏覽器播不動、重編碼」混在一起', () => {
  const m = convertWarning([gcut])
  assert.ok(m.includes('去除空白的 1 個'))
  assert.ok(m.includes('不重新編碼'))
  assert.ok(!m.includes('瀏覽器播不動'))
})

t('遮蔽損壞：講明另存、不刪原檔、要去待決定清單比對', () => {
  const m = convertWarning([{ action: 'damaged', in_place: false }])
  assert.ok(m.includes('遮蔽損壞的 1 個'), m)
  assert.ok(m.includes('「不會」刪除原始檔'), m)
  assert.ok(m.includes('待決定'), m)
})

t('空清單與 undefined 都不會爆', () => {
  assert.ok(convertWarning([]).includes('處理 0 個檔案'))
  assert.ok(convertWarning(undefined).includes('處理 0 個檔案'))
})

console.log(`\n${pass} 通過 / ${fail} 失敗`)
process.exit(fail ? 1 : 0)
