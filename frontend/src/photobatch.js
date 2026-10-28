// Selecting photos for batch deletion.
//
// Pure, and tested (`node tests/photobatch.test.mjs`), because this is the one
// place in the app that deletes many of the user's files at once and there is
// no recycle bin behind it. The dangerous failures here are all quiet:
//
//   * a tick that survives the filter being turned off, so "delete 40" reaches
//     photos that are no longer on screen;
//   * a chunk boundary that skips a file;
//   * a result pass that drops a photo from the grid although the server said
//     it could not be deleted.

/** Toggle one path, returning a NEW set (Vue does not track Set mutation). */
export function toggle(picked, path) {
  const s = new Set(picked)
  s.has(path) ? s.delete(path) : s.add(path)
  return s
}

/** Every visible path, or none. `items` are the cards actually rendered. */
export function selectAll(items, on) {
  return on ? new Set(items.map(m => m.path)) : new Set()
}

/**
 * What a delete would actually touch.
 *
 * The intersection with what is on screen is the safety property, not a
 * tidiness one: selections are made while a filter is on, and the filter can
 * change under them. Anything picked but no longer shown is dropped rather
 * than deleted.
 */
export function targets(shown, picked) {
  return shown.map(m => m.path).filter(p => picked.has(p))
}

/** Split into request-sized batches, preserving order and losing nothing. */
export function chunk(paths, size) {
  if (!(size > 0)) return paths.length ? [paths.slice()] : []
  const out = []
  for (let i = 0; i < paths.length; i += size) out.push(paths.slice(i, i + size))
  return out
}

/**
 * Fold one server reply into the local state.
 *
 * `deleted` and `errors` are independent: a file can fail, and a file the
 * request never reached appears in neither. Only what the server confirms as
 * deleted may leave the grid.
 */
export function applyResult({ items, noPerson, picked }, deleted, errors) {
  const gone = new Set(deleted || [])
  const errs = {}
  for (const e of errors || []) errs[e.path] = e.error
  return {
    items: items.filter(m => !gone.has(m.path)),
    noPerson: noPerson.filter(p => !gone.has(p)),
    // a failed file stays ticked: the retry should be aimed at exactly these
    picked: new Set([...picked].filter(p => !gone.has(p))),
    errors: errs,
    deletedCount: gone.size,
    failedCount: Object.keys(errs).length,
  }
}

/** "已刪除 12 張照片" / "刪除 10 張，2 張失敗（原因標在卡片上）" */
export function resultMessage(deletedCount, failedCount) {
  if (failedCount) {
    return `刪除 ${deletedCount} 張，${failedCount} 張失敗（原因標在卡片上）`
  }
  return `已刪除 ${deletedCount} 張照片`
}
