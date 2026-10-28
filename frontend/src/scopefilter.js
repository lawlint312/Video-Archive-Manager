// Pure selection/filter logic for ScopePicker.
//
// Split out of the component so it can be tested with plain `node` — there is
// no test runner here, and the dangerous part is not the rendering. With a
// filter active, "全不選" must touch only the rows on screen; if it reached
// the hidden ones it would silently clear a 766-folder selection, or hand the
// converter folders the user never looked at.

/**
 * Is this folder currently selected?
 *
 * `flipped` stores the paths that *differ* from the dialog's default, so a
 * folder discovered later inherits that default instead of appearing in an
 * old saved selection.
 */
export function isPicked(flipped, path, defaultAll) {
  return defaultAll ? !flipped.has(path) : flipped.has(path)
}

export function togglePath(flipped, path) {
  const s = new Set(flipped)
  s.has(path) ? s.delete(path) : s.add(path)
  return s
}

/** Set every item in `list` on or off, leaving everything else alone. */
export function applyAll(flipped, list, on, defaultAll) {
  const s = new Set(flipped)
  const differs = on !== defaultAll
  for (const it of list) differs ? s.add(it.path) : s.delete(it.path)
  return s
}

/** Substring match over both name and path, so "E:\" narrows to one drive. */
export function matchesQuery(item, q) {
  const needle = (q || '').trim().toLowerCase()
  if (!needle) return true
  return (item.name || '').toLowerCase().includes(needle)
      || (item.path || '').toLowerCase().includes(needle)
}

/**
 * The rows to display. Filtering never changes the selection — it only
 * changes what is being looked at.
 */
export function visibleItems(list, { q, rootFilter, onlySelected, picked,
                                     useRoot = true } = {}) {
  return list.filter(it => {
    if (useRoot && rootFilter && it.root_label !== rootFilter) return false
    if (onlySelected && !picked(it.path)) return false
    return matchesQuery(it, q)
  })
}

/** Root chips with their row counts, busiest first. */
export function rootCounts(chars) {
  const m = new Map()
  for (const c of chars) m.set(c.root_label, (m.get(c.root_label) || 0) + 1)
  return [...m].map(([label, n]) => ({ label, n }))
                .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label))
}
