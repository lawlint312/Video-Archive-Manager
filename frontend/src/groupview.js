// Per-group display settings: which tabs a character in that group shows, and
// what shape its video cards are.
//
// Split out of the components because two of the rules here are the kind that
// look fine in the source and only bite in front of a user:
//
//   - Hiding both tabs leaves a character page with nothing on it. That is the
//     same shape of bug as the "沒有人物" filter the user got stranded in — the
//     way in still exists, the destination does not.
//   - `default_browse_tab` is a global setting, so it can point at a tab this
//     group has hidden. Opening a character then lands on nothing.
//
// Both are one line each and neither is visible in a build.
//
// Only deviations from the default are stored (see `_clean_group_view` on the
// server, and `groups.js` for the same trick applied to open/closed sections),
// so a group with no entry — and `未分類`, which is derived rather than stored
// and deliberately not configurable — falls through to DEFAULT.

export const DEFAULT = { videos: true, profile: true, ratio: '16:9',
                         poster: false, home: 'circle', albums: false }

// Card shape, and the column width that goes with it. The width is not
// decoration: 4:5 at the 16:9 grid's 350px minimum would make cards 438px
// tall. The portrait numbers are the profile photo grid's, which is what the
// user was pointing at when asking for this.
//
// Measured over the library (samples of 120 per root, 95% intervals): the
// Social Media roots are 57–86% portrait video, JAV and 裏番 are 0%. A 9:16
// video in a 16:9 box shows 32% of the frame; in 4:5 it shows 70%.
export const RATIOS = {
  '16:9': { css: '16 / 9', min: 350, cap: 420, label: '16:9（寬）' },
  '4:5':  { css: '4 / 5',  min: 236, cap: 300, label: '4:5（直式，同個人資料）' },
  '9:16': { css: '9 / 16', min: 200, cap: 260, label: '9:16（直式，手機全幅）' },
}

export const RATIO_KEYS = Object.keys(RATIOS)

// Home-grid card shape. `circle` is the original round avatar tile.
//
// The column minimums are wider than the video grid's equivalents because a
// home card carries a name and a caption under the picture, and because the
// round tile has always been 130px — a 16:9 picture at 130px is only 73px
// tall, which reads as a strip rather than a card.
export const HOME_SHAPES = {
  circle: { css: '1 / 1', min: 130, round: true,  label: '圓形（大頭照）' },
  '16:9': { css: '16 / 9', min: 210, round: false, label: '16:9（寬）' },
  '4:5':  { css: '4 / 5',  min: 150, round: false, label: '4:5（直式）' },
  '9:16': { css: '9 / 16', min: 130, round: false, label: '9:16（直式，手機全幅）' },
}

export const HOME_KEYS = Object.keys(HOME_SHAPES)

/** Rectangular home cards fall back to the folder's cover image. */
export function usesFolderPoster(shape) {
  return !!HOME_SHAPES[shape] && !HOME_SHAPES[shape].round
}

/** CSS custom properties for the home grid. */
export function homeStyle(shape) {
  const s = HOME_SHAPES[shape] || HOME_SHAPES.circle
  return {
    '--home-ratio': s.css,
    '--home-min': `${s.min}px`,
    '--home-radius': s.round ? '50%' : '8px',
  }
}

/**
 * Which picture a home card shows, and whether it is a folder cover.
 *
 * A round tile never uses the folder cover: it is small and neutral enough
 * that the person icon reads as "nobody chose a photo", where an empty
 * rectangle just reads as broken. That is why this is one setting and not two.
 */
export function cardImage(c, shape) {
  if (c && c.avatar) return { src: c.avatar, framed: true }
  if (c && c.poster && usesFolderPoster(shape)) return { src: c.poster, framed: false }
  return null
}

/** Settings for one group name. `null`/未分類/unknown → the defaults. */
export function viewOf(name, view) {
  const v = (name && view && view[name]) || {}
  const out = { ...DEFAULT, ...v }
  if (!RATIOS[out.ratio]) out.ratio = DEFAULT.ratio
  if (!HOME_SHAPES[out.home]) out.home = DEFAULT.home
  // Both tabs off is never a state the rest of the app has to cope with.
  if (!out.videos && !out.profile) out.videos = true
  return out
}

/** Which group a character belongs to, or '' for 未分類. */
export function groupOf(charName, groups) {
  if (!charName || !groups) return ''
  for (const [g, members] of Object.entries(groups))
    if (Array.isArray(members) && members.includes(charName)) return g
  return ''
}

/** Settings for the group a character sits in. */
export function viewForChar(charName, groups, view) {
  return viewOf(groupOf(charName, groups), view)
}

/**
 * The tab to open on, given the user's configured default.
 *
 * Falls back to whichever tab is visible rather than to a fixed one: a group
 * showing only 個人資料 must open there even though the global default is 影片.
 */
export function pickTab(preferred, v) {
  if (preferred === 'profile' && v.profile) return 'profile'
  if (preferred === 'videos' && v.videos) return 'videos'
  return v.videos ? 'videos' : 'profile'
}

/** CSS custom properties for the video grid. */
export function gridStyle(ratio) {
  const r = RATIOS[ratio] || RATIOS[DEFAULT.ratio]
  return {
    '--card-ratio': r.css,
    '--card-min': `${r.min}px`,
    '--card-cap': `${r.cap}px`,
  }
}

/** Strip anything equal to the default, so only deviations are sent. */
export function packView(view) {
  const out = {}
  for (const [g, v] of Object.entries(view || {})) {
    const full = viewOf(g, { [g]: v })
    const diff = {}
    for (const k of Object.keys(DEFAULT))
      if (full[k] !== DEFAULT[k]) diff[k] = full[k]
    if (Object.keys(diff).length) out[g] = diff
  }
  return out
}
