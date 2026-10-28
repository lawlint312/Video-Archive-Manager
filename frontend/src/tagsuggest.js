/**
 * Autocomplete for the comma-separated tag field.
 *
 * The first version of this was a `<datalist>`, which was simply wrong:
 * **datalist matches against the whole value of the input**, and this field
 * holds a list. Once you have typed `JAV, J` the browser compares the string
 * `"JAV, J"` against the tag names and matches nothing — so suggestions only
 * ever worked for the very first tag, which is exactly the case where you
 * least need them.
 *
 * So the matching has to be done against the token under the caret, and that
 * is what this file is. It is pure and tested because the failure mode is
 * "nothing appears" — indistinguishable from having no suggestions to make.
 *
 *   node tests/tagsuggest.test.mjs
 */

export const MAX_SUGGESTIONS = 8

/** The comma-delimited token the caret is sitting in. */
export function tokenAt(value, caret) {
  const v = String(value ?? '')
  const c = Math.max(0, Math.min(caret ?? v.length, v.length))
  const start = v.lastIndexOf(',', c - 1) + 1
  let end = v.indexOf(',', c)
  if (end < 0) end = v.length
  return { start, end, text: v.slice(start, end).trim() }
}

/** Every tag already committed in the field, so they are not offered twice. */
export function committed(value) {
  return String(value ?? '').split(',').map(s => s.trim()).filter(Boolean)
}

/**
 * Which existing tags to offer.
 *
 * Matching ignores case — typing `j` has to find `JAV`, and that was the
 * user's explicit example. What is *stored* is still exactly what was typed:
 * silently rewriting `milk` to an existing `Milk` was offered and declined.
 * Prefix matches rank above matches in the middle, because the thing you are
 * part-way through typing is the thing you most likely mean.
 */
export function suggest(vocab, value, caret, limit = MAX_SUGGESTIONS) {
  const { text } = tokenAt(value, caret)
  const q = text.toLowerCase()
  const taken = new Set(committed(value).map(s => s.toLowerCase()))
  // The token being typed is not "taken" — it is the query.
  taken.delete(q)
  const hits = []
  for (const entry of vocab || []) {
    const [tag, count] = Array.isArray(entry) ? entry : [entry, 0]
    const low = String(tag).toLowerCase()
    if (taken.has(low)) continue
    // An empty token offers everything: clicking into a blank field and
    // seeing what exists is how you find out there is a vocabulary at all.
    const at = q ? low.indexOf(q) : 0
    if (at < 0) continue
    // exact match is not worth offering — it is already what you typed
    if (q && low === q) continue
    hits.push({ tag, count, rank: at })
  }
  hits.sort((a, b) => a.rank - b.rank || b.count - a.count ||
                      String(a.tag).localeCompare(String(b.tag)))
  return hits.slice(0, limit)
}

/** Replace the token under the caret with `tag`, and say where the caret goes. */
export function apply(value, caret, tag) {
  const v = String(value ?? '')
  const { start, end } = tokenAt(v, caret)
  const before = v.slice(0, start)
  const after = v.slice(end)
  // A trailing ", " so the next tag can be typed straight away — but not when
  // something already follows, or every pick would insert a stray comma.
  const tail = after.trim() ? '' : ', '
  const mid = (before && !/[\s]$/.test(before) ? ' ' : '') + tag + tail
  return { value: before + mid + after, caret: (before + mid).length }
}

/** Move the highlighted row, wrapping at both ends. */
export function moveIndex(i, delta, len) {
  if (!len) return -1
  if (i < 0) return delta > 0 ? 0 : len - 1
  return (i + delta + len) % len
}
