/**
 * node tests/groupview.test.mjs   (from frontend/)
 *
 * Per-group display settings.
 *
 * Two of these rules exist because of bugs the user hit, not because they are
 * interesting: a mode you cannot get out of, and a stored default that points
 * at something no longer on screen. Both look perfectly reasonable in the
 * source, and `npm run build` has nothing to say about either.
 */
import { DEFAULT, RATIOS, RATIO_KEYS, HOME_SHAPES, HOME_KEYS, viewOf, groupOf,
         viewForChar, pickTab, gridStyle, homeStyle, usesFolderPoster,
         cardImage, packView } from '../src/groupview.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}

console.log('\n沒設定過的分類拿到預設')
check(viewOf('JAV', {}).ratio === '16:9', '比例預設 16:9')
check(viewOf('JAV', {}).videos && viewOf('JAV', {}).profile, '兩個分頁都在')
check(viewOf('JAV', {}).poster === false, '預設不找同名圖片')
check(viewOf('', {}).ratio === '16:9', '未分類（空字串）也是預設')
check(viewOf(null, null).ratio === '16:9', 'view 還沒載入時不會爆')

console.log('\n只存偏離預設的部分 —— 舊設定檔不用遷移')
// A group added after this feature shipped has no entry at all. It must
// inherit the defaults, not behave oddly because it is missing from a list.
const stored = { '着エロ': { ratio: '4:5' } }
check(viewOf('着エロ', stored).ratio === '4:5', '存了的那項生效')
check(viewOf('着エロ', stored).profile === true, '沒存的欄位補上預設')
check(viewOf('WEB CAM', stored).ratio === '16:9', '完全沒提到的分類 → 預設')

console.log('\n兩個分頁不能同時關掉')
// The user got stranded in an empty "沒有人物" filter for exactly this shape
// of reason: the way in survived, the destination did not.
const both = viewOf('X', { X: { videos: false, profile: false } })
check(both.videos === true, '真的存進來也要救回來，不是只靠對話框擋')
check(both.videos || both.profile, '永遠至少剩一個')

console.log('\n認不得的比例不能傳給 CSS')
// A junk value in `aspect-ratio` is not an error — the declaration is dropped
// and the card collapses to whatever the content gives it.
check(viewOf('X', { X: { ratio: '3:7' } }).ratio === '16:9', '沒聽過的比例 → 16:9')
check(viewOf('X', { X: { ratio: '' } }).ratio === '16:9', '空字串 → 16:9')

console.log('\n角色屬於哪個分類')
const groups = { JAV: ['A', 'B'], '着エロ': ['C'] }
check(groupOf('B', groups) === 'JAV', '找得到')
check(groupOf('Z', groups) === '', '沒被分類的是空字串，不是 undefined')
check(groupOf('C', groups) === '着エロ', '第二個分類')
check(groupOf(null, groups) === '', 'null 不會爆')
check(groupOf('A', null) === '', 'groups 還沒載入不會爆')
check(viewForChar('C', groups, { '着エロ': { ratio: '9:16' } }).ratio === '9:16',
      '角色 → 分類 → 設定')
check(viewForChar('Z', groups, { '着エロ': { ratio: '9:16' } }).ratio === '16:9',
      '未分類的角色一律預設 —— 未分類不可設定')

console.log('\n開哪個分頁')
const V = (v, p) => ({ ...DEFAULT, videos: v, profile: p })
check(pickTab('videos', V(true, true)) === 'videos', '兩個都在 → 照使用者的預設')
check(pickTab('profile', V(true, true)) === 'profile', '同上，另一個方向')
// The global 預設分頁 setting knows nothing about groups.
check(pickTab('profile', V(true, false)) === 'videos',
      '預設是個人資料但這個分類關掉了 → 退回影片')
check(pickTab('videos', V(false, true)) === 'profile',
      '預設是影片但這個分類關掉了 → 退回個人資料')
check(pickTab('nonsense', V(true, true)) === 'videos', '認不得的預設值 → 影片')
check(pickTab(undefined, V(false, true)) === 'profile',
      '設定還沒載入時也不能落在被隱藏的分頁上')

console.log('\n比例連帶欄寬 —— 只改 aspect-ratio 會做出 438px 高的巨卡')
for (const k of RATIO_KEYS) {
  const s = gridStyle(k)
  check(!!s['--card-ratio'] && !!s['--card-min'] && !!s['--card-cap'],
        `${k} 三個變數都有`)
}
const w = gridStyle('16:9'), t = gridStyle('4:5'), p = gridStyle('9:16')
check(parseInt(t['--card-min']) < parseInt(w['--card-min']),
      '4:5 的欄寬要比 16:9 窄')
check(parseInt(p['--card-min']) < parseInt(t['--card-min']),
      '9:16 又比 4:5 更窄')
// height = width / ratio; keep every shape roughly within one screen
for (const k of RATIO_KEYS) {
  const r = RATIOS[k]
  const [a, b] = r.css.split('/').map(Number)
  check(r.cap / (a / b) <= 480, `${k} 卡片最高 ${Math.round(r.cap / (a / b))}px，沒有失控`)
}
check(gridStyle('3:7')['--card-ratio'] === RATIOS['16:9'].css,
      '認不得的比例退回 16:9，不是 undefined')

console.log('\n主頁卡片形狀')
check(viewOf('X', {}).home === 'circle', '預設是圓形 —— 現況不變')
check(HOME_KEYS[0] === 'circle', '圓形排在第一個')
check(viewOf('X', { X: { home: '9:16' } }).home === '9:16', '設定得動')
check(viewOf('X', { X: { home: 'oval' } }).home === 'circle',
      '認不得的形狀退回圓形，不是餵給 CSS')
for (const k of HOME_KEYS) {
  const s = homeStyle(k)
  check(!!s['--home-ratio'] && !!s['--home-min'] && !!s['--home-radius'],
        `${k} 三個變數都有`)
}
check(homeStyle('circle')['--home-radius'] === '50%', '圓形是 50% 圓角')
check(homeStyle('4:5')['--home-radius'] !== '50%', '方形不是圓的')
check(homeStyle('oval')['--home-ratio'] === HOME_SHAPES.circle.css,
      '認不得的形狀退回圓形，不是 undefined')
// 16:9 at the round tile's 130px would be 73px tall — a strip, not a card
check(HOME_SHAPES['16:9'].min > HOME_SHAPES.circle.min,
      '16:9 的欄寬要比圓形寬，否則卡片只有 73px 高')
for (const k of HOME_KEYS) {
  const s = HOME_SHAPES[k]
  const [a, b] = s.css.split('/').map(Number)
  check(s.min / (a / b) <= 300,
        `${k} 圖片高度 ${Math.round(s.min / (a / b))}px，一頁還放得下幾排`)
}

console.log('\n資料夾封面只在方形卡片生效')
// A round tile is small and neutral enough that the person icon reads as
// "nobody chose a photo"; an empty rectangle just reads as broken. 89% of
// characters (555 of 623) have no 大頭照, so this is the common case.
check(!usesFolderPoster('circle'), '圓形不吃資料夾封面')
check(usesFolderPoster('16:9') && usesFolderPoster('4:5') && usesFolderPoster('9:16'),
      '三種方形都吃')
check(!usesFolderPoster('nonsense'), '認不得的形狀不吃')

console.log('\n卡片要顯示哪張圖')
const withAva = { avatar: 'A.jpg', poster: 'P.jpg' }
const onlyPos = { poster: 'P.jpg' }
const neither = {}
check(cardImage(withAva, 'circle').src === 'A.jpg', '有大頭照就用大頭照')
check(cardImage(withAva, '4:5').src === 'A.jpg', '方形也一樣 —— 封面只是備援')
check(cardImage(withAva, 'circle').framed === true,
      '大頭照要套使用者裁切過的框')
check(cardImage(onlyPos, 'circle') === null,
      '圓形沒有大頭照 → 人像佔位圖，不用封面')
check(cardImage(onlyPos, '9:16').src === 'P.jpg', '方形沒有大頭照 → 用封面')
check(cardImage(onlyPos, '9:16').framed === false,
      '封面沒有存過焦點，所以是單純置中裁切')
check(cardImage(neither, '16:9') === null, '兩個都沒有 → 佔位圖')
check(cardImage(null, '16:9') === null, 'null 不會爆')

console.log('\n送出前把等於預設的丟掉')
const packed = packView({
  A: { videos: true, profile: true, ratio: '16:9', poster: false, home: 'circle' },
  B: { videos: true, profile: true, ratio: '4:5', poster: false, home: 'circle' },
  C: { videos: false, profile: true, ratio: '16:9', poster: true, home: 'circle' },
  D: { videos: true, profile: true, ratio: '16:9', poster: false, home: '9:16' },
  E: { videos: true, profile: true, ratio: '16:9', poster: false, home: 'circle',
       albums: true },
})
check(!('A' in packed), '整組都是預設的分類根本不必存')
check(JSON.stringify(packed.B) === JSON.stringify({ ratio: '4:5' }),
      '只存真的改過的欄位')
check(packed.C.videos === false && packed.C.poster === true,
      '兩個偏離值都留著')
check(JSON.stringify(packed.D) === JSON.stringify({ home: '9:16' }),
      '只改主頁形狀也只存那一欄')
check(JSON.stringify(packed.E) === JSON.stringify({ albums: true }),
      '相簿開關也只存那一欄')
check(Object.keys(packed).length === 4, '只剩四項')
check(viewOf('X', {}).albums === false, '相簿預設關 —— 上線當天維持平鋪')
check(JSON.stringify(packView({})) === '{}', '空的就是空的')
check(JSON.stringify(packView(null)) === '{}', 'null 不會爆')
// round trip
check(viewOf('B', packed).ratio === '4:5' && viewOf('B', packed).profile === true,
      '存進去再讀出來還是同一件事')

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
