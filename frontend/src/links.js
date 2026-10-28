/**
 * Turning a profile URL into a chip: which site, and whose account.
 *
 * This used to be eight lines inside ProfileTab that took the first path
 * segment as the handle. Real data says that is wrong for a quarter of the
 * links in the library — `youtube.com/channel/UCZgf…` gives "channel",
 * `xhamster.com/creators/demo_kitten` gives "creators". The handle is only
 * worth showing if it is right, so the rule moved here where it can be tested
 * against the actual URLs.
 *
 *   node tests/links.test.mjs
 */

// Label per site. The bottom five were added after a survey found 8 of the
// library's 37 links (five sites) falling through to a bare hostname — and
// they were exactly the sites this library uses.
export const HOSTS = [
  [/(^|\.)instagram\.com$/, 'IG'],
  [/(^|\.)onlyfans\.com$/, 'OnlyFans'],
  [/(^|\.)fansly\.com$/, 'Fansly'],
  [/(^|\.)(x|twitter)\.com$/, 'X'],
  [/(^|\.)reddit\.com$/, 'Reddit'],
  [/(^|\.)patreon\.com$/, 'Patreon'],
  [/(^|\.)(t|telegram)\.me$/, 'TG'],
  [/(^|\.)tiktok\.com$/, 'TikTok'],
  [/(^|\.)(youtube\.com|youtu\.be)$/, 'YouTube'],
  [/(^|\.)twitch\.tv$/, 'Twitch'],
  [/(^|\.)weibo\.(com|cn)$/, '微博'],
  [/(^|\.)threads\.(net|com)$/, 'Threads'],
  [/(^|\.)pixiv\.net$/, 'Pixiv'],
  [/(^|\.)fanbox\.cc$/, 'Fanbox'],
  [/(^|\.)manyvids\.com$/, 'ManyVids'],
  [/(^|\.)facebook\.com$/, 'FB'],
  [/(^|\.)stripchat\.com$/, 'Stripchat'],
  [/(^|\.)xhamster\.(com|desi)$/, 'xHamster'],
  [/(^|\.)eporner\.com$/, 'ePorner'],
  [/(^|\.)simpcity\.(cr|su)$/, 'SimpCity'],
  [/(^|\.)furuke\.com$/, 'Furuke'],
]

export const COLORS = {
  onlyfans: '#00AFF0', instagram: '#DD2A7B', ig: '#DD2A7B', reddit: '#FF4500',
  patreon: '#F96854', tiktok: '#000', twitter: '#000', x: '#000',
  telegram: '#229ED9', tg: '#229ED9', weibo: '#E6162D', fansly: '#2899F5',
  threads: '#101010', youtube: '#FF0000', twitch: '#9146FF', pixiv: '#0096FA',
  fanbox: '#F0B72E', manyvids: '#13A2DC', fb: '#1877F2', facebook: '#1877F2',
  微博: '#E6162D',
  stripchat: '#E8365D', xhamster: '#E67E22', eporner: '#C0392B',
  simpcity: '#5A67D8', furuke: '#6B8E23',
}

// Path segments that are route names, not people. Without this the handle for
// `youtube.com/channel/UC…` is "channel" — a label that is both wrong and the
// same on every YouTube channel link, which is worse than showing nothing.
const ROUTE = new Set([
  'channel', 'c', 'user', 'users', 'profile', 'profiles', 'creators',
  'creator', 'model', 'models', 'threads', 'thread', 'p', 'video', 'videos',
  'watch', 'account', 'onlyfans',
])

export function parseLink(raw) {
  const t = String(raw ?? '').trim()
  if (!/^https?:\/\//i.test(t)) {
    return { label: t, url: null, key: t.toLowerCase(), handle: '' }
  }
  let u
  try { u = new URL(t) } catch {
    return { label: t, url: null, key: t.toLowerCase(), handle: '' }
  }
  const host = u.hostname.replace(/^www\./, '').toLowerCase()
  const hit = HOSTS.find(([re]) => re.test(host))
  const label = hit ? hit[1] : host
  const handle = handleFrom(u)
  return {
    label, url: t, key: label.toLowerCase(), handle,
    title: handle ? `${label} · ${handle}\n${t}` : t,
  }
}

function handleFrom(u) {
  const segs = u.pathname.split('/').map(decodeSafe).filter(Boolean)
  // walk past route names; a segment starting with @ is always the handle
  let i = 0
  while (i < segs.length && !segs[i].startsWith('@') &&
         ROUTE.has(segs[i].toLowerCase())) i++
  const seg = segs[i] || ''
  if (!seg) return ''
  // One '@' and only one: TikTok/YouTube/Threads carry their own, Instagram
  // and the rest do not, and a row of chips reads as a set only if they agree.
  return '@' + seg.replace(/^@+/, '')
}

function decodeSafe(s) {
  try { return decodeURIComponent(s) } catch { return s }
}

export function linkColor(key) { return COLORS[key] || '#7c7cf0' }

/**
 * What each chip should read, given every link on one profile.
 *
 * The handle is always shown (the user's call). Measured first, because it was
 * a real fork: only 2 characters of 627 have two accounts on one site, so
 * "disambiguate only on collision" would have left 625 profiles untouched —
 * the user preferred every link to say who it points at.
 */
export function linkChips(links) {
  return (links || []).map(l => {
    const p = parseLink(l)
    return { ...p, text: p.handle ? `${p.label} ${p.handle}` : p.label }
  })
}
