// Translating the server's text on arrival.
//
//     node tests/serverstrings.test.mjs     (from frontend/)
import { ts, FIXED, TEMPLATES } from '../src/serverstrings.js'
import { setLocale, t } from '../src/i18n.js'
import en from '../src/locales/en.js'
import zhCN from '../src/locales/zh-CN.js'

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

console.log('every entry has a translation')
for (const [code, table] of [['en', en], ['zh-CN', zhCN]]) {
  check(`${code}: fixed`, FIXED.filter(k => !(k in table)), [])
  check(`${code}: templates`, TEMPLATES.filter(k => !(k in table)), [])
}

console.log('\nzh-TW is the server text itself')
setLocale('zh-TW')
check('fixed', ts('唯讀'), '唯讀')
check('template', ts('連續 5 次失敗，已開始鎖定'), '連續 5 次失敗，已開始鎖定')
check('unknown', ts('某個新訊息'), '某個新訊息')

console.log('\nEnglish')
setLocale('en')
check('fixed', ts('唯讀'), en['唯讀'])
check('a role someone typed stays as typed', ts('整理員'), '整理員')
check('template with a number', ts('連續 5 次失敗，已開始鎖定'),
      en['連續 {n} 次失敗，已開始鎖定'].replace('{n}', '5'))
check('a value that is itself a known string',
      ts('鎖定中（帳號+位址，還有 42 秒）'),
      en['鎖定中（{why}，還有 {n} 秒）'].replace('{why}', en['帳號+位址']).replace('{n}', '42'))
check('the more specific template wins', ts('剪輯 a.mp4（3 段）'),
      t('剪輯 {name}（{n} 段）', { name: 'a.mp4', n: '3' }))
check('… and the plain one still works', ts('剪輯 a.mp4'),
      en['剪輯 {name}'].replace('{name}', 'a.mp4'))
check('a 、 list of known strings', ts('轉檔掃描（含 MP4、完整解碼檢查）'),
      en['轉檔掃描（{list}）'].replace('{list}', [en['含 MP4'], en['完整解碼檢查']].join(en['、'])))
check('a file name is never translated, even one that reads like a server word',
      ts('歸檔 唯讀'), en['歸檔 {name}'].replace('{name}', '唯讀'))
check('an error with a server sentence inside it',
      ts('請先修改密碼：密碼還缺少：小寫英文字母、數字'),
      t('請先修改密碼：{reason}', {
        reason: t('密碼還缺少：{list}', { list: [en['小寫英文字母'], en['數字']].join(en['、']) }) }))
check('a date error keeps the typed text and translates the example',
      ts('「31/31/2020」分不出哪個是月、哪個是日。請用 年-月-日，例如 2026-10-22'),
      t('「{text}」分不出哪個是月、哪個是日。{hint}',
        { text: '31/31/2020', hint: en['請用 年-月-日，例如 2026-10-22'] }))
check('an OS error inside stays verbatim',
      ts('備份失敗：[Errno 13] Permission denied'),
      t('備份失敗：{error}', { error: '[Errno 13] Permission denied' }))
check('a convert reason is translated finding by finding',
      ts('需重編碼（視訊為 mpeg4）；驗證後會取代原檔'),
      t('需重編碼（視訊為 {codec}）', { codec: 'mpeg4' }) + en['；'] + en['驗證後會取代原檔'])
check('… including a finding that is itself fixed text',
      ts('影格時序錯誤（有 B 影格卻沒有顯示時間表，瀏覽器會掉格）；需要安裝 GPAC 才能修復'),
      en['影格時序錯誤（有 B 影格卻沒有顯示時間表，瀏覽器會掉格）'] + en['；'] + en['需要安裝 GPAC 才能修復'])
check('a ； inside text nobody listed is left alone', ts('甲；乙'), '甲；乙')
check('a progress line with the check name translated',
      ts('檢查格式：a.mkv（3/10，另 5 支沿用上次結果）'),
      t('{check}：{name}（{k}/{total}，另 {n} 支沿用上次結果）',
        { check: en['檢查格式'], name: 'a.mkv', k: '3', total: '10', n: '5' }))
check('a verify problem inside the failure',
      ts('驗證失敗，已保留原檔：輸出檔沒有視訊軌'),
      t('驗證失敗，已保留原檔：{problem}', { problem: en['輸出檔沒有視訊軌'] }))
check('empty', ts(''), '')
check('null', ts(null), '')

setLocale('zh-TW')
console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
