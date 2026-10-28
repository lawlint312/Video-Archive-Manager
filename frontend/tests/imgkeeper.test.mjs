/**
 * node tests/imgkeeper.test.mjs   (from frontend/)
 *
 * The image keeper ladder, now with watermarks. The cases are the shapes
 * measured in Misa_av, not invented ones: the old ladder kept the
 * watermarked copy in 42 of 52 groups because watermarking re-saved the
 * picture at 2-3x the bytes.
 */
import { suggestKeeper, wmLabel, CLEAN_MAX } from '../src/imgkeeper.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
const f = (name, o) => ({ path: name, name, label: '歸檔/x', width: 2160,
  height: 2880, size: 1_000_000, mtime: 100, ...o })
const keep = files => suggestKeeper(files).keeper?.path ?? null

console.log('\nMisa_av 的主要形狀：同一張圖，乾淨的檔案比較小')
const clean = f('clean', { size: 1_587_000, wm: 0 })
const marked = f('marked', { size: 3_725_000, wm: 0.014 })
check(keep([clean, marked]) === 'clean', '留乾淨的，即使有浮水印那張大兩倍多')
check(suggestKeeper([clean, marked]).why === '無浮水印', '理由寫「無浮水印」')
check(keep([marked, clean]) === 'clean', '跟排列順序無關')

console.log('\n沒有浮水印資料（舊報告）時，行為跟以前一模一樣')
const a = f('a', { size: 1_587_000 }), b = f('b', { size: 3_725_000 })
check(keep([a, b]) === 'b', '照舊留檔案較大的')
const n1 = f('n1', { wm: null }), n2 = f('n2', { wm: null, size: 2_000_000 })
check(keep([n1, n2]) === 'n2', 'wm 是 null（判斷不了）也照舊')

console.log('\n「無浮水印」排在像素之前（使用者的規則）')
const smallClean = f('smallClean', { width: 899, height: 1200, wm: 0 })
const bigMarked = f('bigMarked', { wm: 0.012 })
check(keep([smallClean, bigMarked]) === 'smallClean', '乾淨但小的勝過大但有浮水印的')

console.log('\n「浮水印較小」排在像素之後')
// #43–47: both marked, the big one charged about twice the area
const big = f('big', { wm: 0.0165 })
const small = f('small', { width: 899, height: 1200, wm: 0.0081 })
check(keep([big, small]) === 'big',
      '兩張都有浮水印時，2160×2880 不會為了浮水印小一點輸給 899×1200')
check(suggestKeeper([big, small]).why === '像素較多', '理由寫「像素較多」')
const same1 = f('same1', { wm: 0.016, size: 3_000_000 })
const same2 = f('same2', { wm: 0.006, size: 2_000_000 })
check(keep([same1, same2]) === 'same2', '同樣大小時，浮水印明顯較小的勝過檔案較大的')
const close1 = f('close1', { wm: 0.010, size: 3_000_000 })
const close2 = f('close2', { wm: 0.009, size: 2_000_000 })
check(keep([close1, close2]) === 'close1', '差不多大的浮水印不算數，退回檔案大小')

console.log('\n判斷不了的照片不影響別人，也不被誤判成乾淨')
const unk = f('unk', { wm: null, size: 5_000_000 })
check(keep([clean, marked, unk]) === 'clean', '有量到乾淨的就留它')
check(keep([marked, unk]) === 'unk', '沒有任何一張被量成乾淨，就不走這一步')

console.log('\n位置仍然最優先')
const inboxClean = f('inboxClean', { label: '待分類', wm: 0 })
const archMarked = f('archMarked', { wm: 0.02 })
check(keep([inboxClean, archMarked]) === 'archMarked', '歸檔的勝過待分類的')

console.log('\n卡片標籤')
check(wmLabel({ wm: 0 }) === '無浮水印', '0 → 無浮水印')
check(wmLabel({ wm: CLEAN_MAX / 2 }) === '無浮水印', '門檻以下也算乾淨')
check(wmLabel({ wm: 0.0123 }) === '浮水印 1.2%', '百分比一位小數')
check(wmLabel({ wm: 0.0005 }).includes('浮水印'), '門檻以上但很小也要標')
check(wmLabel({ wm: null }) === '' && wmLabel({}) === '', '沒資料就不顯示')

console.log(fail ? `\nFAIL (${fail})` : '\nPASS')
process.exit(fail ? 1 : 0)
