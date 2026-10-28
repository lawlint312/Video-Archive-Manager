/**
 * node tests/wikisections.test.mjs   (from frontend/)
 *
 * The wiki's section names, shown in Chinese.
 *
 * The point of the test is the second half: what is *stored* must stay the
 * wiki's own Japanese, because that string is also the grid's filter key
 * (`wiki:sec:単体作品`) and is already sitting in the database from earlier
 * imports. Translating on the way in would strand all of it.
 */
import { sectionName, hasTranslation, SECTION_NAMES }
  from '../src/wikisections.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

console.log('\n實際頁面上出現過的每一個區段都要有中文')
for (const jp of ['単体作品', '総集編作品', 'VR作品', '配信作品',
                  '同人サークル作品', 'イメージ作品',
                  'その他作品(DVD・BD)', 'その他作品(配信系)']) {
  check(hasTranslation(jp), `${jp} → ${sectionName(jp)}`)
}

console.log('\n使用者選定的兩個用詞')
check(sectionName('総集編作品') === '總集編', '総集編作品 → 總集編')
check(sectionName('イメージ作品') === '寫真影片', 'イメージ作品 → 寫真影片')

console.log('\n查不到的原樣顯示，不猜')
check(sectionName('謎の新セクション') === '謎の新セクション',
      '沒收錄的區段照原文顯示 —— 猜一個讀起來像樣的比留著原文更糟')
check(sectionName('') === '' && sectionName(undefined) === '' &&
      sectionName(null) === '', '空值不會變成 "undefined"')

console.log('\n翻譯只是顯示，鍵還是日文')
check(!Object.values(SECTION_NAMES).includes('単体作品'),
      '中文那一側不會反過來對到日文')
for (const [jp, zh] of Object.entries(SECTION_NAMES)) {
  if (jp === zh) continue
  check(sectionName(zh) === zh,
        `已經翻過的 ${zh} 再翻一次還是自己 —— 不會被二次替換`)
  break
}
check(Object.keys(SECTION_NAMES).every(k => k === k.trim()),
      '鍵沒有前後空白，不然永遠對不上伺服器送來的字串')

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
