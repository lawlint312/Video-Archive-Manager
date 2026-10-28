/**
 * The segment list behind the clip dialog.
 *
 * Extracted for one reason: `mount.test.mjs` renders every component but calls
 * no click handler, so a list operation left inside the component is untested
 * by construction — which is how the list shipped able to play and delete a
 * segment but not to change one.
 */

/** Add a segment, or replace the one being edited. */
export function upsert(segments, editingId, start, end, newId) {
  if (editingId === null || editingId === undefined) {
    return [...segments, { id: newId, start, end }]
  }
  return segments.map(s => s.id === editingId ? { ...s, start, end } : s)
}

/** Remove by id, leaving the rest in order. */
export function drop(segments, id) {
  return segments.filter(s => s.id !== id)
}

/**
 * Whether a pair may be committed.
 *
 * One second is `clip.MIN_SECONDS` on the server. Checking it here means the
 * button is disabled rather than the submit being rejected after the user has
 * already stopped thinking about it.
 */
export function commitable(start, end) {
  return start !== null && start !== undefined
      && end !== null && end !== undefined
      && end - start >= 1
}
