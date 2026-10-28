/**
 * "Watched" and "favourite" marks: the parts worth testing.
 *
 * The API calls are one line each and live at the bottom. What is actually
 * easy to get wrong is *when* a video counts as watched, and that is a small
 * state machine with a clock in it — so it is here, injectable, rather than a
 * setTimeout buried in a component where the only way to check it is to sit in
 * front of a browser with a stopwatch.
 *
 *   node tests/videoflags.test.mjs
 */

// explicit extension: node's ESM resolver needs it (tests import this file)
import { relSummary, relLabel } from './relinfo.js'

// Five seconds of actual playback. Not "the player was opened": on a phone a
// mis-tap opens and closes a video in well under a second, and there is no way
// to tell that apart from a real view if opening is the trigger. Five seconds
// is long enough that nothing accidental reaches it and short enough that
// glancing at a video to see what it is does count — which is exactly the
// judgement this mark is meant to record while clearing out the inbox.
export const WATCH_SECONDS = 5

/**
 * Tracks playback towards the threshold.
 *
 * `play()` / `pause()` mirror the media element's events; `tick(now)` is the
 * clock. It fires `onReach` once and then stays quiet — re-watching must not
 * re-send, because the stored timestamp is the *first* view and the request
 * would be pure noise.
 */
export function createWatchGate({ seconds = WATCH_SECONDS, onReach } = {}) {
  let played = 0        // accumulated ms of playback
  let since = null      // when the current playing stretch began, or null
  let fired = false

  function settle(now) {
    if (since !== null) {
      played += now - since
      since = now
    }
  }

  return {
    play(now) { if (!fired && since === null) since = now },
    // Pausing keeps what has been played so far rather than resetting it.
    // Someone who plays four seconds, pauses to read the memo, then plays two
    // more has watched this video; a gate that reset would never mark it.
    pause(now) { settle(now); since = null },
    tick(now) {
      if (fired) return false
      settle(now)
      if (played < seconds * 1000) return false
      fired = true
      onReach?.()
      return true
    },
    get elapsed() { return played },
    get done() { return fired },
  }
}

/**
 * The listing filter.
 *
 * A mark you cannot filter on is decoration — this is the half that makes the
 * feature worth having, and the inbox (2,016 videos) is where it earns its
 * keep. Unknown modes fall through to everything rather than to nothing: a
 * stale value in localStorage should not present an empty library.
 */
export const FILTERS = [
  ['all', '全部'],
  ['unwatched', '未看過'],
  ['fav', '我的最愛'],
]

export function filterVideos(videos, mode) {
  if (mode === 'unwatched') return videos.filter(v => !v.watched)
  if (mode === 'fav') return videos.filter(v => v.fav)
  // Modes set by clicking a number in the wiki comparison panel. They share
  // this one slot rather than being a second, independent filter: the header
  // shows one chip saying what you are looking at, and two overlapping
  // filters would make that chip a lie.
  //
  // `dupe` deliberately returns *both* copies of a duplicated number, not the
  // surplus one — the reason to look at them is to choose between them.
  if (mode === 'wiki:extra') return videos.filter(v => v.wiki_state === 'extra')
  if (mode === 'wiki:none') return videos.filter(v => v.wiki_state === 'none')
  if (mode === 'wiki:dupe') return videos.filter(v => v.wiki_dupe)
  if (mode?.startsWith('wiki:sec:')) {
    const sec = mode.slice('wiki:sec:'.length)
    return videos.filter(v => v.wiki_section === sec && v.wiki_state === 'have')
  }
  return videos
}

/** Is this one of the filters the wiki panel sets? */
export function isWikiMode(mode) {
  return typeof mode === 'string' && mode.startsWith('wiki:')
}

// ── server ──────────────────────────────────────────────────────────────────
// Marks are the reader's own record, so these are not gated on edit mode: they
// have to work for a viewer account, and on the phone where the editing surface
// is hidden entirely.

export async function postWatched(api, path, on = true) {
  return api('/api/flags/watched', {
    method: 'POST', body: JSON.stringify({ path, on }),
  })
}

export async function postFav(api, path, on) {
  return api('/api/flags/fav', {
    method: 'POST', body: JSON.stringify({ path, on }),
  })
}

/**
 * Which marks a card should show.
 *
 * A favourite is never shown alongside the eye. You favourite something after
 * watching it, so the eye adds nothing next to a heart — it is one more thing
 * in a 40px corner saying what the heart already said. The record still holds
 * both flags; this is only about what is worth drawing.
 *
 * Returns [] when there is nothing to draw, so the caller can skip the
 * container entirely rather than leaving an empty box over the thumbnail.
 */
export function markIcons(v) {
  if (!v) return []
  if (v.fav) return [{ key: 'fav', icon: 'heart', cls: 'mk fav', title: '我的最愛' }]
  if (v.watched) return [{ key: 'seen', icon: 'eye', cls: 'mk seen', title: '看過' }]
  return []
}

/**
 * The card's other corner: the number of a film this one is cut from,
 * compiled into, or holds (AVBase) — text, bottom-left, apart from the marks
 * above (the user's layout, 2026-10-27). null when there is none.
 */
export function relTag(v) {
  if (!v || !v.rel || !v.rel.length) return null
  return { text: relLabel(v.rel), title: '收錄關係：' + relSummary(v.rel) }
}

/**
 * What stands between this account and deleting `v`, because of favourites.
 *
 * `own`    — your favourite: un-favourite it yourself first (unchanged).
 * `others` — only other accounts' favourites. An admin may delete after a
 *            confirmation that says how many; anyone else is told to ask one.
 * `none`   — nothing.
 *
 * The server decides again on its own (main._fav_refusal); this only lets the
 * card say so *before* the click instead of after it.
 */
export function favGuard(v, role) {
  if (v && v.fav) return { kind: 'own', count: 0, canForce: false }
  const n = Number(v && v.fav_others) || 0
  if (!n) return { kind: 'none', count: 0, canForce: false }
  return { kind: 'others', count: n, canForce: role === 'admin' }
}

/** The text for each case; one wording for the grid and the preview panel. */
export function favGuardMessage(name, g, where = '開啟影片、在備忘那一列') {
  if (g.kind === 'own') {
    return `「${name}」已標為我的最愛，不會被刪除。\n\n` +
           `如果確定要刪，請先${where}點一下愛心取消最愛。`
  }
  if (g.kind === 'others' && g.canForce) {
    return `「${name}」有 ${g.count} 位使用者加入了我的最愛。\n\n` +
           `刪除後他們的最愛也會一起消失，而且無法復原。\n` +
           `這次刪除會記錄在「登入紀錄 → 值得看一眼的事件」。\n\n確定要刪除嗎？`
  }
  if (g.kind === 'others') {
    return `「${name}」有 ${g.count} 位使用者加入了我的最愛，不會被刪除。\n\n` +
           `只有管理員可以確認後刪除。`
  }
  return ''
}
