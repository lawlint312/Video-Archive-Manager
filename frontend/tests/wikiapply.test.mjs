/**
 * node tests/wikiapply.test.mjs   (from frontend/)
 *
 * What the wiki import actually sends.
 *
 * Every assertion here exists because of a bug that shipped: a field the
 * server reads and the client forgot to send. That failure is invisible —
 * nothing errors, the import reports success, and the missing data shows up
 * later as some unrelated-looking symptom ("this character's wiki data is an
 * old import").
 */
import { buildApplyBody, FIELDS, WIKI_FIELDS, IAFD_FIELDS, AVBASE_FIELDS }
  from '../src/wikiapply.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

const page = {
  name: '森田花梨',
  url: 'https://seesaawiki.jp/w/sougouwiki/d/x',
  titles: { 'NSPS-694': 'あ' },
  aliases: ['浅井花梨'],
  cast: { 'NSPS-694': ['森田花梨'] },
  summary: { total: 750, no_id: 8, sections: [{ name: '単体作品', count: 543 }] },
  all_ids: ['NSPS-694'],
  works: [{ i: 'NSPS-694', t: 'あ', d: '2018/04/13', s: '単体作品' }],
}
const all = { folder: 'X:\JAV\森田花梨', titles: true, aliases: true, cast: true }

console.log('\n每一個伺服器會讀的欄位都要送出去')
const body = buildApplyBody(page, all)
for (const f of WIKI_FIELDS) {
  check(f in body, `有送 ${f}`)
}
check(Object.keys(body).length === WIKI_FIELDS.length,
      '而且沒有多送 —— 多出來的欄位代表兩邊對這個請求的認知不一致')

console.log('\n統計與作品清單不受勾選影響')
// The bug: `works` was never sent at all, so the missing-films list was always
// empty and reported the import as an old one.
check(body.works.length === 1, 'works 有送（漏掉它，缺少清單會永遠是空的）')
check(body.summary.total === 750, 'summary 有送')
check(body.all_ids.length === 1, 'all_ids 有送')

console.log('\n什麼都不勾，統計照樣要存得起來')
// The other bug: `folder` was only sent when aliases were ticked, and the
// server needs it to file the summary against a character at all.
const none = buildApplyBody(page, { folder: 'X:\JAV\森田花梨' })
check(none.folder === 'X:\JAV\森田花梨',
      '沒勾任何東西時 folder 還是要送 —— 不然統計無處可歸')
check(none.works.length === 1 && none.summary.total === 750,
      '統計與作品清單照樣送')
check(Object.keys(none.titles).length === 0 && none.aliases.length === 0 &&
      Object.keys(none.cast).length === 0,
      '但沒勾的那些是空的，不會偷偷寫進去')

console.log('\n只勾片名時不會動到 person_info.json')
const t = buildApplyBody(page, { folder: 'X:\a', titles: true })
check(Object.keys(t.titles).length === 1, '片名有送')
check(t.aliases.length === 0,
      '別名是空的 —— 伺服器據此決定不碰 NAS 上的檔案')

console.log('\n沒有對應角色時 folder 是空字串，不是 undefined')
const noFolder = buildApplyBody(page, { titles: true })
check(noFolder.folder === '', 'folder 空字串')
check(noFolder.summary !== undefined, 'summary 仍然送（片名跟角色無關）')

console.log('\n殘缺的預覽結果不能讓它爆掉')
const bare = buildApplyBody({}, {})
for (const f of WIKI_FIELDS) check(f in bare, `空頁面也有 ${f}`)
check(bare.works.length === 0 && bare.summary === null, '空的就是空的')

console.log()

// ── IAFD ──────────────────────────────────────────────────────────────────
// A different shape from the same function, and that is the point: sending a
// wiki body for an IAFD page would write nothing and report success.
{
  const page = {
    site: 'iafd.com', name: 'Rosalie',
    profile: { dob: '1993-05-27', height: '162 cm' },
    matched: [{ path: 'D:/a/x.mp4', title: 'Applauding Myself', year: '2014' },
              { path: 'D:/a/y.mp4', title: 'Date with Herself', year: '2012' }],
    // Fields a wiki page would carry — they must NOT leak into the body.
    titles: { 'ABC-1': 'nope' }, aliases: ['nope'], cast: { 'ABC-1': ['nope'] },
  }
  const body = buildApplyBody(page, { folder: 'D:/a', profile: true, released: true })
  for (const f of IAFD_FIELDS) check(f in body, `IAFD body 有 ${f}`)
  check(Object.keys(body).length === IAFD_FIELDS.length,
        `IAFD body 只有 ${IAFD_FIELDS.length} 個欄位（實際 ${Object.keys(body).length}）`)
  check(body.site === 'iafd.com', 'site 要送出，伺服器靠它分流')
  check(!('titles' in body) && !('aliases' in body) && !('cast' in body),
        'IAFD 不寫片名、別名與共演者')
  check(body.file_released['D:/a/x.mp4'] === '2014', '比對到的檔案要帶年份')
  check(Object.keys(body.file_released).length === 2, '只送比對到的')

  const off = buildApplyBody(page, { folder: 'D:/a' })
  check(Object.keys(off.profile).length === 0, '沒勾個人資料就不送')
  check(Object.keys(off.file_released).length === 0, '沒勾發行日就不送')
}

console.log('\nAVBase')
{
  const page = {
    site: 'avbase.net', url: 'https://www.avbase.net/works/AKO-599',
    titles: { 'UFD-061': '働く美女と性交 浅井花梨' }, released: { 'UFD-061': '2016-08-04' },
    cast: { 'UFD-061': ['浅井花梨'] }, aliases: ['浅井花梨'],
    profile: { dob: '1997-01-10' },
    relations: [{ parent: 'UFD-061', child: 'AKO-599', parent_date: '2016-08-04',
                  child_date: '2026-03-07', parent_title: 'x', child_title: 'y',
                  kind: 'original', have_parent: true, have_child: false }],
    summary: { total: 1 },
    works: [{ i: 'UFD-061', t: '働く美女と性交 浅井花梨', d: '2016-08-04', s: 'AVBase:単体' }],
  }
  const all = { folder: 'D:/a', titles: true, cast: true, aliases: true,
                profile: true, relations: true }
  const body = buildApplyBody(page, all)
  for (const f of AVBASE_FIELDS) check(f in body, `AVBase body 有 ${f}`)
  check(Object.keys(body).length === AVBASE_FIELDS.length,
        `AVBase body 只有 ${AVBASE_FIELDS.length} 個欄位（實際 ${Object.keys(body).length}）`)
  check(AVBASE_FIELDS.every(f => FIELDS.includes(f)), 'FIELDS 涵蓋 AVBase 的欄位')
  check(body.relations[0].parent === 'UFD-061' && !('kind' in body.relations[0])
        && !('have_parent' in body.relations[0]),
        '關係只送事實（番號、日期、片名），判斷由伺服器重算')
  check(!('summary' in body) && !('all_ids' in body), 'wiki 的統計欄位不混進來')
  check(body.works.length === 1 && body.catalog === true,
        '作品清單送進共用目錄（之後下載的片也有片名）')
  const none = buildApplyBody(page, { folder: 'D:/a' })
  check(none.works.length === 1 && none.catalog === false,
        '不勾片名：清單照送（收錄統計要用），但片名不存')
  check(Object.keys(none.titles).length === 0 && none.relations.length === 0
        && none.aliases.length === 0 && Object.keys(none.profile).length === 0,
        '沒勾的都不送')
}

if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('PASS')
