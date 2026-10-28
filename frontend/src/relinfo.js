/**
 * How a film relates to others (AVBase's relations), in words — for the
 * player's 收錄 row and the card's mark.
 *
 * A relation is stored once (parent ⊃ child) and read from either end, so
 * each verdict has two phrasings. The verdicts are core/avbase.py `judge`:
 *   original  the parent came first; the child is cut from it
 *   uncut     the parent is a later compilation holding the child whole
 *   digest    the parent is a compilation of excerpts of the child
 *   unknown   no dates to tell which
 *
 *   node tests/relinfo.test.mjs
 */
const AS_CHILD = {     // this film is the child: what the other one is to it
  original: '擷取自',
  uncut: '完整收錄於',
  // the user's wording (2026-10-27): 「片段收錄於」 read badly
  digest: '被選入精選集',
  unknown: '收錄於',
}
const AS_PARENT = {    // this film is the parent
  original: '被擷取成',
  uncut: '完整收錄',     // the user's wording, not 「完整收錄了」
  // ends before the number: the row reads "verb → number → title"
  digest: '片段來自',
  unknown: '包含',
}
const NOTE = {
  original: '原片',
  uncut: '總集編',
  digest: '精選總集編',
  unknown: '缺發行日，無法判斷哪部是原片',
}

/** The words before the other film's number. */
export function relVerb(kind, thisIsParent) {
  return (thisIsParent ? AS_PARENT : AS_CHILD)[kind] || (thisIsParent ? '包含' : '收錄於')
}

export function relNote(kind) {
  return NOTE[kind] || ''
}

/**
 * The card's one-line summary, from the compact form the video list sends
 * ([{o, k, p}]): what it comes from first, then how many it holds.
 * `擷取自 UFD-061`, `收錄於 3 部`, `擷取自 UFD-061 · 被擷取成 2 部`.
 */
// its own origin first: "where is this from" is the question a card answers
function byOrigin(rel) {
  return [...rel].sort((a, b) => (a.p ? 1 : 0) - (b.p ? 1 : 0))
}

/**
 * The card's mark, as text (the user's choice over an icon, 2026-10-27): the
 * related film's number — its origin first — and `+N` for the rest.
 * `UFD-061`, `UFD-061 +2`.
 */
export function relLabel(rel) {
  if (!rel || !rel.length) return ''
  const first = byOrigin(rel)[0].o
  return rel.length > 1 ? `${first} +${rel.length - 1}` : first
}

export function relSummary(rel) {
  if (!rel || !rel.length) return ''
  const parts = []
  const groups = new Map()
  for (const r of byOrigin(rel)) {
    const verb = relVerb(r.k, r.p)
    if (!groups.has(verb)) groups.set(verb, [])
    groups.get(verb).push(r.o)
  }
  for (const verb of groups.keys()) {
    const ids = groups.get(verb)
    parts.push(ids.length === 1 ? `${verb} ${ids[0]}` : `${verb} ${ids.length} 部`)
  }
  return parts.join(' · ')
}
