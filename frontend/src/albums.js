/**
 * The profile tab's album view: one card per first-level subfolder.
 *
 * Photo-set characters keep one set per subfolder — `Yeha (예하)` has 28 of
 * them, 2,665 photos, and 141 of 761 characters have two or more. The flat
 * grid mixes every set into one sheet. This groups by the `album` field the
 * server already tags each file with (media.scan_profile_albums), so it costs
 * no extra I/O.
 *
 * Pure, and tested (`node tests/albums.test.mjs`): `mount.test.mjs` renders the
 * profile tab but never clicks into an album, and a grouping that is wrong
 * looks exactly like one that is right until you count the photos.
 */
import { naturalCompare, baseName } from './photosort.js'

/** The flat grid: exactly what it was before albums existed. */
export function flatItems(items) {
  return (items || []).filter(m => !m.in_set)
}

// Same names, same order, as the folder cover on the home grid — a set that
// ships its own cover image should get it on the card.
const COVER_NAMES = ['poster', 'folder', 'cover']

function stem(path) {
  return baseName(path).replace(/\.[^.]*$/, '').toLowerCase()
}

/** Which item a card shows: a named cover, else the first photo by name. */
export function pickCover(items) {
  for (const n of COVER_NAMES) {
    const hit = items.find(m => !m.is_video && stem(m.path) === n)
    if (hit) return hit
  }
  const byName = [...items].sort((a, b) =>
    naturalCompare(baseName(a.path), baseName(b.path)))
  // a set that is only videos (Yeha's `PPV`) still gets a picture: the video
  // thumbnail
  return byName.find(m => !m.is_video) || byName[0] || null
}

export const LOOSE_NAME = '未分組照片'

/** Group items into albums. Each album carries what its card needs. */
export function buildAlbums(items) {
  const map = new Map()
  for (const m of items || []) {
    const key = m.album || ''
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(m)
  }
  const out = []
  for (const [key, list] of map) {
    const kind = key === '' ? 'loose' : (key.toLowerCase() === 'ig' ? 'ig' : 'set')
    out.push({
      key,
      kind,
      name: kind === 'loose' ? LOOSE_NAME : key,
      items: list,
      photos: list.filter(m => !m.is_video).length,
      videos: list.filter(m => m.is_video).length,
      size: list.reduce((s, m) => s + (m.size || 0), 0),
      mtime: list.reduce((t, m) => Math.max(t, m.mtime || 0), 0),
      cover: pickCover(list),
    })
  }
  return out
}

/**
 * Whether the album view applies. The group asks for it, AND the character
 * has at least two subfolders holding photos: one album is the flat grid
 * behind an extra click. Videos-only folders do not count toward the two —
 * they were never on this tab before, and two PPV folders are not a photo set.
 */
export function albumsApply(enabled, albums) {
  if (!enabled) return false
  return albums.filter(a => a.kind !== 'loose' && a.photos > 0).length >= 2
}

export const ALBUM_NATURAL_DIR = { name: 'asc', newest: 'desc', count: 'desc' }

/**
 * Order the cards. The loose photos and IG are not sets, so they sit first
 * whatever the sort — pinned rather than shuffled into Vol.163…Vol.265.
 */
export function sortAlbums(albums, by = 'name', dir = 'asc') {
  const rank = { loose: 0, ig: 1, set: 2 }
  const cmp = {
    name: (a, b) => naturalCompare(a.name, b.name),
    newest: (a, b) => a.mtime - b.mtime,
    count: (a, b) => (a.photos + a.videos) - (b.photos + b.videos),
  }[by] || ((a, b) => naturalCompare(a.name, b.name))
  const sign = dir === 'desc' ? -1 : 1
  return [...albums].sort((a, b) =>
    (rank[a.kind] - rank[b.kind]) || sign * cmp(a, b) ||
    naturalCompare(a.name, b.name))
}

/** Next state when an album sort button is clicked. */
export function nextAlbumSort(cur, by) {
  if (cur.by === by) return { by, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
  return { by, dir: ALBUM_NATURAL_DIR[by] || 'asc' }
}

/** "98 張 · 2 部" — the card caption. */
export function albumCaption(a) {
  const parts = []
  if (a.photos) parts.push(`${a.photos} 張`)
  if (a.videos) parts.push(`${a.videos} 部影片`)
  return parts.join(' · ')
}
