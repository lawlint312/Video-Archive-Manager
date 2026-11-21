// Folder lists in Settings (inbox folders, archive roots): the rules behind
// FolderListEditor.vue, kept here so node can test them.
//
// They used to be one textarea each, a path per line. With UNC paths that
// wrap and a box four lines tall, nobody could see what was in the list.

// Windows paths compare without case and without a trailing separator:
// `D:\JAV\` and `d:\jav` are the same folder. A drive root keeps its
// separator, since `D:` alone means "D's current directory".
export function folderKey(p) {
  let s = String(p || '').trim().replace(/\//g, '\\')
  if (!/^[A-Za-z]:\\$/.test(s)) s = s.replace(/\\+$/, '')
  return s.toLowerCase()
}

export function cleanFolder(p) {
  let s = String(p || '').trim().replace(/^"(.*)"$/, '$1').trim()
  if (/^[A-Za-z]:$/.test(s)) s += '\\'
  else if (!/^[A-Za-z]:\\$/.test(s)) s = s.replace(/[\\/]+$/, '')
  return s
}

// Adds what was typed or pasted: several lines at once is fine (pasting the
// old textarea's contents works). Returns the new list and what was skipped
// because it was already in it, so the editor can say so instead of
// silently doing nothing.
export function addFolders(list, text) {
  const out = [...list]
  const seen = new Set(out.map(folderKey))
  const added = []
  const dupes = []
  for (const line of String(text || '').split(/\r?\n/)) {
    const p = cleanFolder(line)
    if (!p) continue
    const k = folderKey(p)
    if (seen.has(k)) { dupes.push(p); continue }
    seen.add(k)
    out.push(p)
    added.push(p)
  }
  return { list: out, added, dupes }
}

export function moveFolder(list, i, delta) {
  const j = i + delta
  if (j < 0 || j >= list.length) return list
  const out = [...list]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out
}

// The folder above `p`, or '' when there is nothing to list above it: a
// drive root goes to the drive list, and `\\host\share` cannot go up
// (Windows will not list a host's shares by walking it).
export function parentFolder(p) {
  const s = cleanFolder(p).replace(/\//g, '\\')
  if (!s || /^[A-Za-z]:\\$/.test(s)) return ''
  const unc = s.match(/^\\\\[^\\]+\\[^\\]+$/)
  if (unc) return ''
  const i = s.lastIndexOf('\\')
  if (i < 0) return ''
  const up = s.slice(0, i)
  if (/^[A-Za-z]:$/.test(up)) return up + '\\'
  if (/^\\\\[^\\]*$/.test(up)) return ''
  return up
}

// Where the folder browser offers to start: the folders above the ones
// already configured (a NAS share cannot be reached from the drive list, so
// this is how it gets there without typing), then the configured ones.
export function startPlaces(paths) {
  const out = []
  const seen = new Set()
  const add = p => {
    const k = folderKey(p)
    if (p && !seen.has(k)) { seen.add(k); out.push(p) }
  }
  for (const p of paths) add(parentFolder(p))
  return out
}

export function joinFolder(base, name) {
  if (!base) return name
  return base.replace(/[\\/]+$/, '') + '\\' + name
}
