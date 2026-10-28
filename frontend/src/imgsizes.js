/**
 * Natural sizes of the home grid's avatars, reported as they load.
 *
 * The grid needs each picture's size to frame it (avatar.js), and it used to
 * replace its whole size table on every image `load` — and the whole grid
 * depends on that table, so every load re-rendered every card. Unnoticed with
 * ~50 avatars in JAV; with Gfriends there are 201 in a 228-card section, and
 * opening it on a phone meant 201 renders of 228 cards (~46,000) before it
 * settled. Collapsing and reopening did it all again: the <img> elements are
 * new, so every one fires `load` once more. (2026-10-28, reported on mobile.)
 *
 * Two fixes, both here:
 *   * a size already known is not reported again — reopening a section
 *     changes nothing, so it renders nothing
 *   * the sizes that arrive within one frame are applied together
 *
 *   node tests/imgsizes.test.mjs
 */
export function sizeBatcher(getKnown, apply,
                            schedule = (f) => (globalThis.requestAnimationFrame
                              || ((g) => setTimeout(g, 16)))(f)) {
  let pending = null
  return function report(key, w, h) {
    if (!w || !h) return
    const seen = (pending && pending[key]) || getKnown()[key]
    if (seen && seen.w === w && seen.h === h) return
    if (!pending) {
      pending = {}
      schedule(() => {
        const batch = pending
        pending = null
        apply(batch)
      })
    }
    pending[key] = { w, h }
  }
}
