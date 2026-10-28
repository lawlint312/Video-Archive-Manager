/**
 * node tests/links.test.mjs   (from frontend/)
 *
 * Every URL below is a real one from the library. That matters: the previous
 * implementation took the first path segment as the handle, which reads fine
 * and is wrong for a quarter of them — and wrong in the worst way, because
 * `youtube.com/channel/UC…` produced the label "channel" on every YouTube
 * link, so they all looked identical while appearing to be identified.
 */
import { parseLink, linkChips, linkColor, HOSTS } from '../src/links.js'

let fail = 0
const check = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg)
  if (!cond) fail++
}
const h = u => parseLink(u).handle
const l = u => parseLink(u).label

console.log('\n站名')
check(l('https://www.instagram.com/sample_wifey/') === 'IG', 'instagram → IG')
check(l('https://x.com/lina_sample1') === 'X', 'x.com → X')
check(l('https://twitter.com/foo') === 'X', 'twitter.com 也是 X')
check(l('https://www.youtube.com/@demo-channel') === 'YouTube', 'youtube')
check(l('https://www.tiktok.com/@sample_user22') === 'TikTok', 'tiktok')

console.log('\n先前認不出來的五個站（庫裡 37 個連結中的 8 個）')
check(l('https://zt.stripchat.com/Sampleee/') === 'Stripchat', '子網域也算')
check(l('https://zh.stripchat.com/-sample-') === 'Stripchat', '另一個子網域')
check(l('https://xhamster.com/creators/demo_kitten/') === 'xHamster', 'xhamster')
check(l('https://www.eporner.com/profile/Samplename00') === 'ePorner', 'eporner')
check(l('https://simpcity.cr/threads/minami-minami.1679705/') === 'SimpCity', 'simpcity')
check(l('https://www.furuke.com/Demo0101') === 'Furuke', 'furuke')

console.log('\n帳號 —— 路由名稱不能當成人名')
check(h('https://www.instagram.com/sample_wifey/') === '@sample_wifey', 'IG')
check(h('https://x.com/lina_sample1') === '@lina_sample1', 'X')
check(h('https://www.furuke.com/Demo0101') === '@Demo0101', '第一段就是帳號')
check(h('https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa')
      === '@UCaaaaaaaaaaaaaaaaaaaaaa',
      'youtube 的 /channel/ 要跳過 —— 舊版會顯示成「channel」')
check(h('https://xhamster.com/creators/demo_kitten/') === '@demo_kitten',
      '/creators/ 要跳過')
check(h('https://www.eporner.com/profile/Samplename00') === '@Samplename00',
      '/profile/ 要跳過')
check(h('https://simpcity.cr/threads/minami-minami.1679705/')
      === '@minami-minami.1679705', '/threads/ 要跳過')

console.log('\n@ 只留一個')
check(h('https://www.youtube.com/@demo-channel') === '@demo-channel',
      '本來就有 @ 的不會變成 @@')
check(h('https://www.tiktok.com/@jane_doe19') === '@jane_doe19', 'tiktok')
check(h('https://www.threads.com/@hana.tanaka_life') === '@hana.tanaka_life',
      'threads')
// A user whose name really is a route word would be hidden by the skip list,
// so the @ form has to win over it.
check(h('https://example.com/@profile') === '@profile',
      '帳號叫 profile 但寫成 @profile 時，@ 優先於跳過清單')

console.log('\n沒有帳號可取的情況')
check(h('https://www.instagram.com/') === '', '只有網域 → 沒有帳號')
check(h('https://example.com') === '', '完全沒有路徑')
check(parseLink('https://example.com').label === 'example.com',
      '不認得的站顯示網域')
check(parseLink('https://www.example.com/x').label === 'example.com',
      'www. 去掉')

console.log('\n不是網址的東西不能爆')
for (const bad of ['中出', '', null, undefined, 'https://', 'http://[']) {
  const p = parseLink(bad)
  check(typeof p.label === 'string' && 'handle' in p,
        `${JSON.stringify(bad)} → 回傳完整結構`)
}

console.log('\n晶片文字：站名 + 帳號（使用者選擇一律顯示）')
const chips = linkChips([
  'https://www.instagram.com/yuko_hoshikawa0101',
  'https://www.instagram.com/yukohoshikawa101/',
])
check(chips[0].text === 'IG @yuko_hoshikawa0101', chips[0].text)
check(chips[1].text === 'IG @yukohoshikawa101', chips[1].text)
check(chips[0].text !== chips[1].text,
      '星川優子的兩個 IG 終於分得出來 —— 這是原本的問題')
check(linkChips(['https://example.com'])[0].text === 'example.com',
      '取不到帳號時只顯示站名，不留一個空的 @')

console.log('\n配色')
check(linkColor('ig') === '#DD2A7B', '認得的站有自己的顏色')
check(linkColor('stripchat') !== linkColor('xhamster'), '新加的五個站不同色')
check(linkColor('nope') === '#7c7cf0', '不認得的用預設色')

console.log('\n每個站名都要有對應的顏色鍵，否則新加的站是預設紫')
for (const [, label] of HOSTS) {
  check(linkColor(label.toLowerCase()) !== '#7c7cf0', `${label} 有顏色`)
}

console.log()
if (fail) { console.log(`${fail} FAILED`); process.exit(1) }
console.log('all good')
