// Batch-deleting photos.
//
//     node tests/photobatch.test.mjs        (from frontend/)
//
// This is the only place in the app that deletes many files in one action, and
// there is no recycle bin. Every case here is a way to delete the wrong thing
// or to hide the fact that something was not deleted.
import {
  toggle, selectAll, targets, chunk, applyResult, resultMessage,
} from '../src/photobatch.js'

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}
const item = p => ({ path: p })
const P = ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg']
const items = P.map(item)

console.log('勾選')
check('勾一個', [...toggle(new Set(), 'a.jpg')], ['a.jpg'])
check('再勾一次就取消', [...toggle(new Set(['a.jpg']), 'a.jpg')], [])
{
  // Vue does not react to Set mutation, so a returned-in-place set would make
  // the ticks stop repainting — visibly wrong, but only in the browser
  const before = new Set(['a.jpg'])
  const after = toggle(before, 'b.jpg')
  check('回傳的是新的 Set', after === before, false)
  check('原本的沒被改動', [...before], ['a.jpg'])
}
check('全選', [...selectAll(items, true)], P)
check('全不選', [...selectAll(items, false)], [])

console.log('\n只能刪掉「畫面上而且勾了」的')
check('交集', targets(items, new Set(['a.jpg', 'c.jpg'])), ['a.jpg', 'c.jpg'])
// the important one: a tick made before the filter changed
check('勾過但已經不在畫面上的不會被刪',
      targets([item('a.jpg')], new Set(['a.jpg', 'zzz.jpg'])), ['a.jpg'])
check('什麼都沒勾就是空的', targets(items, new Set()), [])
check('順序照畫面上的順序',
      targets(items, new Set(['d.jpg', 'a.jpg'])), ['a.jpg', 'd.jpg'])

console.log('\n分批')
check('剛好整除', chunk(['1', '2', '3', '4'], 2), [['1', '2'], ['3', '4']])
check('除不盡也不會掉東西', chunk(['1', '2', '3'], 2), [['1', '2'], ['3']])
check('比一批還少', chunk(['1'], 25), [['1']])
check('空的', chunk([], 25), [])
{
  // a size of 0 must not loop forever or silently drop everything
  const c = chunk(['1', '2'], 0)
  check('批次大小 0 也要把全部送出去', c.flat(), ['1', '2'])
}
{
  const all = Array.from({ length: 204 }, (_, i) => `p${i}.jpg`)
  const c = chunk(all, 25)
  check('204 張分成 9 批', c.length, 9)
  check('加起來還是 204 張，順序不變', c.flat(), all)
}

console.log('\n把伺服器的回應套回畫面')
{
  const state = { items: P.map(item), noPerson: [...P], picked: new Set(P) }
  const r = applyResult(state, ['a.jpg', 'b.jpg'],
                        [{ path: 'c.jpg', error: '檔案使用中' }])
  check('刪掉的離開格子', r.items.map(m => m.path), ['c.jpg', 'd.jpg'])
  check('刪掉的也離開「沒有人物」清單', r.noPerson, ['c.jpg', 'd.jpg'])
  // c failed and d was never answered for — both must stay armed so the retry
  // hits exactly them
  check('失敗的仍然勾著', [...r.picked], ['c.jpg', 'd.jpg'])
  check('錯誤訊息掛在該檔案上', r.errors, { 'c.jpg': '檔案使用中' })
  check('計數', [r.deletedCount, r.failedCount], [2, 1])
}
{
  // the failure that would be worst: a photo vanishing from the grid although
  // the server refused to delete it
  const state = { items: P.map(item), noPerson: [...P], picked: new Set(P) }
  const r = applyResult(state, [], [{ path: 'a.jpg', error: '拒絕' }])
  check('一個都沒刪成時，格子完全不變', r.items.length, 4)
  check('而且會回報失敗', [r.deletedCount, r.failedCount], [0, 1])
}
{
  const state = { items: P.map(item), noPerson: [...P], picked: new Set() }
  const r = applyResult(state, undefined, undefined)
  check('伺服器沒回這兩個欄位也不能爆', [r.items.length, r.deletedCount], [4, 0])
}

console.log('\n結果訊息')
check('全成功', resultMessage(12, 0), '已刪除 12 張照片')
check('有失敗就要說', resultMessage(10, 2),
      '刪除 10 張，2 張失敗（原因標在卡片上）')
check('一張都沒刪成', resultMessage(0, 3),
      '刪除 0 張，3 張失敗（原因標在卡片上）')

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
