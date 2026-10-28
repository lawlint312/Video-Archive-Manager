// Video filename search.
//
//     node tests/videosearch.test.mjs        (from frontend/)
//
// The interesting part is the catalogue-number handling: this library is full
// of names like STARS-786.mp4, and people type stars786, "stars 786" or
// stars_786. Matching only the literal string finds none of those.

globalThis.localStorage = {
  getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {},
}

const V = await import('../src/videosearch.js')

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

V.videoIndex.dirs = ['E:\\JAV\\森田花梨', 'I:\\Web Cam\\guoguo-77', 'C:\\Downloads']
V.videoIndex.rows = [
  ['STARS-786.mp4', '森田花梨', 0, 0, 'Rich Sperm Dedicated To A Genius Girl'],
  ['STARS-787.mp4', '森田花梨', 0, 0, ''],
  ['STARS-788.mp4', '森田花梨', 0, 0, ''],
  ['STARS-789.mp4', '森田花梨', 0, 0, ''],
  ['MIDV-087.mp4', '森田花梨', 0, 0, 'Sweetly Sadistic Temptress'],
  ['guoguo-77-20260727.mp4', 'guoguo-77', 1, 0, ''],
  ['未整理的檔案 stars 900.mp4', 'guoguo-77', 1, 0, ''],
  ['SSIS-123.mp4', '', 2, 1, ''],
]
V.videoIndex.ready = true

console.log('plain substring')
{
  const r = V.searchVideos('midv')
  check('found it', r.total, 1)
  check('under the right character',
        [...r.byChar.keys()], ['森田花梨'])
}

console.log('\ncatalogue numbers, however you type them')
for (const q of ['STARS-786', 'stars786', 'stars 786', 'stars_786']) {
  const r = V.searchVideos(q)
  const names = [...r.byChar.values()].flatMap(v => v.shown.map(s => s[0]))
  check(`"${q}"`, names.includes('STARS-786.mp4'), true)
}

console.log('\nnormalisation does not run wild on short queries')
{
  // "st" would normalise-match almost everything; under 3 chars stays literal
  const r = V.searchVideos('st')
  // 4× STARS-78x, "… stars 900", and MIDV-087 whose title has "Sadistic"
  check('literal only', r.total, 6)
}

console.log('\none noisy character cannot bury the rest')
{
  const r = V.searchVideos('stars')
  const d = r.byChar.get('森田花梨')
  check('counts everything', d.count, 4)
  check('but shows only a few', d.shown.length, 3)
  check('other characters still present', r.byChar.has('guoguo-77'), true)
}

console.log('\ninbox is kept separate — those belong to nobody yet')
{
  const r = V.searchVideos('ssis')
  check('not filed under a character', r.byChar.size, 0)
  check('listed as inbox', r.inbox.map(x => x[0]), ['SSIS-123.mp4'])
}

console.log('\nvery short queries do nothing')
{
  check('single character', V.searchVideos('s').total, 0)
  check('empty', V.searchVideos('   ').total, 0)
}

console.log('\npath reconstruction')
{
  check('windows separator', V.fullPath(['STARS-786.mp4', '森田花梨', 0, 0]),
        'E:\\JAV\\森田花梨\\STARS-786.mp4')
  V.videoIndex.dirs.push('/mnt/media/x')
  check('posix separator', V.fullPath(['a.mp4', 'x', 3, 0]),
        '/mnt/media/x/a.mp4')
}

console.log('\nCJK survives normalisation')
{
  const r = V.searchVideos('未整理')
  check('found', r.total, 1)
}

console.log('\nbest match first')
{
  // typed the exact number: STARS-786 must be shown even though three
  // weaker STARS-78x hits come before it in folder order
  V.videoIndex.rows = [
    ['STARS-7860.mp4', 'A', 0, 0, ''],
    ['未整理 STARS-786 中.mp4', 'A', 0, 0, ''],
    ['STARS-786 中文字幕.mp4', 'A', 0, 0, ''],
    ['STARS-786.mp4', 'A', 0, 0, ''],
  ]
  const r = V.searchVideos('stars786', { perChar: 3 })
  check('exact, then prefix, then contains',
        r.byChar.get('A').shown.map(x => x[0]),
        ['STARS-786.mp4', 'STARS-7860.mp4', 'STARS-786 中文字幕.mp4'])
  check('count still covers all of them', r.byChar.get('A').count, 4)
}

console.log('\nseveral words: each must be somewhere in the row')
V.videoIndex.rows = [
  ['STARS-786.mp4', '三浦真央', 0, 0, ''],
  ['SSIS-001.mp4', '三浦真央', 0, 0, 'Dedicated Girl'],
  ['STARS-100.mp4', '森田花梨', 0, 0, 'Dedicated To A Genius Girl'],
]
{
  check('character + catalogue prefix',
        [...V.searchVideos('三浦 stars').byChar.keys()], ['三浦真央'])
  check('words in any order, across title',
        [...V.searchVideos('girl genius').byChar.keys()], ['森田花梨'])
  // the character name alone matches every one of her files — not a hit
  check('character words only find nothing',
        V.searchVideos('三浦 真央').total, 0)
  check('one word missing is a miss', V.searchVideos('三浦 midv').total, 0)
  check('a whole-string hit still wins over splitting',
        V.searchVideos('stars 786').byChar.get('三浦真央').shown[0][0],
        'STARS-786.mp4')
}

console.log('\na new index is searched, not the old one')
{
  V.videoIndex.rows = [['ABP-001.mp4', 'X', 0, 0, '']]
  check('old index', V.searchVideos('abp').total, 1)
  V.videoIndex.rows = [['ABP-001.mp4', 'X', 0, 0, ''], ['ABP-002.mp4', 'X', 0, 0, '']]
  check('replaced index', V.searchVideos('abp').total, 2)
}

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
