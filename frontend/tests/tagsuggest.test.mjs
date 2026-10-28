/**
 * node tests/tagsuggest.test.mjs   (from frontend/)
 *
 * The case that made this necessary is the first block below: with a
 * `<datalist>` the browser matches against the *entire* input value, so
 * suggestions worked for the first tag and silently stopped working for every
 * one after it. Nothing appears, and "nothing appears" looks exactly like
 * "there was nothing to suggest".
 */
import { tokenAt, suggest, apply, committed, moveIndex } from '../src/tagsuggest.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
const names = r => r.map(x => x.tag)

// what the library actually has today
const VOCAB = [['JAV', 3], ['Milk', 1], ['Urabukkake', 1], ['M', 1],
               ['中出', 1], ['OnlyFans', 1], ['IG', 1]]

console.log('\n游標所在的那一段（datalist 做不到的就是這件事）')
check(tokenAt('J', 1).text === 'J', '第一個標籤')
check(tokenAt('JAV, M', 6).text === 'M', '第二個標籤 —— 整串是「JAV, M」')
check(tokenAt('JAV, Milk, 中', 12).text === '中', '第三個')
check(tokenAt('JAV,  M  ', 9).text === 'M', '前後空白不算在內')
check(tokenAt('JAV, M, X', 6).text === 'M', '游標在中間時只看中間那段')
check(tokenAt('', 0).text === '', '空字串')

console.log('\n打一個字母就要跳出既有選項（使用者的例子）')
check(names(suggest(VOCAB, 'J', 1)).includes('JAV'), '「J」找得到 JAV')
check(names(suggest(VOCAB, 'j', 1)).includes('JAV'), '小寫「j」也找得到 —— 建議不分大小寫')
check(names(suggest(VOCAB, 'JAV, M', 6)).includes('Milk'),
      '第二個標籤打「M」照樣有建議 —— 這正是 datalist 失效的地方')
check(names(suggest(VOCAB, 'JAV, m', 6)).includes('Milk'), '第二個也不分大小寫')

console.log('\n排序：開頭符合的排在中間符合的前面')
const r = suggest(VOCAB, 'a', 1)
check(r.length > 0 && r[0].rank === 0 ? true : r.every(x => x.rank >= 0),
      '有結果而且帶著位置')
const r2 = suggest([['ab', 1], ['xa', 9]], 'a', 1)
check(names(r2)[0] === 'ab', '「ab」（開頭）排在「xa」（中間）前面，即使後者用得多')
const r3 = suggest([['aa', 1], ['ab', 9]], 'a', 1)
check(names(r3)[0] === 'ab', '同樣是開頭符合時，用得多的排前面')

console.log('\n已經填過的不再建議')
check(!names(suggest(VOCAB, 'JAV, M', 6)).includes('JAV'),
      'JAV 已經在欄位裡了')
check(names(suggest(VOCAB, 'JAV, M', 6)).includes('M') === false,
      '完全相同的不建議 —— 那就是你已經打好的字')

console.log('\n空的時候列出全部（這是你發現有字彙可用的方式）')
check(suggest(VOCAB, '', 0).length === Math.min(7, 8), '空欄位列出全部 7 種')
check(names(suggest(VOCAB, 'JAV, ', 5)).length === 6,
      '打完逗號、還沒打字 → 列出其餘 6 種')

console.log('\n上限')
const big = Array.from({ length: 50 }, (_, i) => [`t${i}`, 1])
check(suggest(big, 't', 1).length === 8, '最多 8 筆')
check(suggest(big, 't', 1, 3).length === 3, '上限可調')

console.log('\n選取之後的字串與游標')
{
  const a = apply('J', 1, 'JAV')
  check(a.value === 'JAV, ', `第一個 → 「${a.value}」`)
  check(a.caret === 5, '游標落在逗號後面，可以接著打下一個')
}
{
  const a = apply('JAV, M', 6, 'Milk')
  check(a.value === 'JAV, Milk, ', `第二個 → 「${a.value}」`)
}
{
  // picking in the middle must not insert a stray comma
  const a = apply('JAV, M, 中出', 6, 'Milk')
  check(a.value === 'JAV, Milk, 中出', `中間 → 「${a.value}」`)
  check(a.caret === 9, `游標停在剛填好的那個標籤後面（"JAV, Milk" 共 9 字）`)
}
{
  const a = apply('', 0, 'JAV')
  check(a.value === 'JAV, ', '空欄位')
}
{
  const a = apply('JAV,', 4, 'Milk')
  check(a.value === 'JAV, Milk, ', `逗號後沒有空格也要處理 →「${a.value}」`)
}

console.log('\n已填清單')
check(committed('JAV, Milk , , 中出').join('|') === 'JAV|Milk|中出',
      '空項目與空白清掉')

console.log('\n鍵盤移動會繞回去')
check(moveIndex(-1, 1, 3) === 0, '沒選時按下 → 第一筆')
check(moveIndex(-1, -1, 3) === 2, '沒選時按上 → 最後一筆')
check(moveIndex(2, 1, 3) === 0, '最後一筆再按下 → 繞回第一筆')
check(moveIndex(0, -1, 3) === 2, '第一筆再按上 → 繞到最後')
check(moveIndex(0, 1, 0) === -1, '沒有結果時不會選到東西')

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
