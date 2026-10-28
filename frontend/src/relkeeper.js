/**
 * Which file to keep in a Tier 3 group: the same footage under another
 * catalogue number (AVBase's relations, backend core/dedup.py).
 *
 * The parent — the original, or the uncut compilation — holds every child
 * whole, so it is the one to keep. Never certain enough for a batch (the
 * user's call, 2026-10-27): there is no batch for these at all. A group is
 * marked uncertain when a child is sharper than the parent, because the
 * "cut" is sometimes a 4K remaster better than the original it came from.
 *
 *   node tests/relkeeper.test.mjs
 */
const px = f => (f.width || 0) * (f.height || 0)

/** `ladder` picks among several copies of the parent (dedup.suggestKeeper). */
export function suggestRelKeeper(files, ladder) {
  const live = files.filter(f => !f.deleted)
  const parents = live.filter(f => f.role === 'parent')
  // the original gone: what is left are different films, nothing to choose
  if (!parents.length) return { keeper: null, certain: true, why: '原片已刪除' }
  if (live.length < 2) return { keeper: parents[0], certain: true, why: '' }
  const k = parents.length === 1 ? parents[0] : ladder(parents).keeper
  if (!k) return { keeper: null, certain: false, why: '原片有好幾份，先決定留哪一份' }
  if (live.some(f => f.role === 'child' && px(f) > px(k))) {
    return { keeper: k, certain: false, why: '擷取版的畫質比原片高' }
  }
  return { keeper: k, certain: true, why: '' }
}

/** What each verdict means, for a group's header (core/avbase.py `judge`). */
export const REL_KIND = { original: '原片 → 擷取', uncut: '完整收錄的總集編' }
