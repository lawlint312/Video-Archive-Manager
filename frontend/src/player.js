import { onMounted, onBeforeUnmount, nextTick, ref } from 'vue'
import { store } from './store'

/**
 * Close the player before the file it is showing gets deleted.
 *
 * This is housekeeping, not a fix: leaving a player pointed at a file that no
 * longer exists just produces a broken element. Deleting used to fail while
 * the player held the file open, but that is now handled on the server, which
 * serves bounded slices and never keeps a handle open across a network wait —
 * so this no longer has to race anything, and deliberately does not sleep.
 *
 * `close` must unmount the player, not just pause it.
 */
export async function releaseFile(close) {
  close()
  await nextTick()
}

// ── volume / mute memory ─────────────────────────────────────────────────────
// Playback starts muted by default; once the viewer touches the volume (or
// unmutes) that choice is remembered for every video afterwards.
const VOL_KEY = 'vam.player.volume'
const MUTE_KEY = 'vam.player.muted'

export function loadPlayerAudio() {
  const v = parseFloat(localStorage.getItem(VOL_KEY))
  const m = localStorage.getItem(MUTE_KEY)
  return {
    volume: Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 1,
    muted: m === null ? true : m === '1',      // default: muted
  }
}

export function savePlayerAudio(volume, muted) {
  localStorage.setItem(VOL_KEY, String(volume))
  localStorage.setItem(MUTE_KEY, muted ? '1' : '0')
}

/**
 * YouTube/Netflix-style keyboard shortcuts for a <video> element.
 *
 * The listener lives on `document` (capture phase) rather than on the <video>,
 * because the video element usually does NOT have focus — that's why binding to
 * it silently fell through to the browser default (Chrome seeks 1% of duration,
 * i.e. 72s on a 2-hour file).
 *
 * Shortcuts:
 *   Space / K   play-pause          ← / →   seek ∓ store.seekSeconds
 *   J / L       seek ∓ 10s          ↑ / ↓   volume ±5%
 *   M           mute                F       fullscreen
 *   0-9         jump to 0%-90%      Home/End start / end
 *   < / >       playback rate       Esc     close
 */
export function usePlayerHotkeys(getVideo, onClose) {
  const toast = ref('')          // small on-screen feedback, like YouTube
  let toastTimer = null

  function flash(msg) {
    toast.value = msg
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { toast.value = '' }, 900)
  }

  function fmt(sec) {
    const s = Math.max(0, Math.floor(sec))
    const m = Math.floor(s / 60)
    const r = s % 60
    return `${m}:${String(r).padStart(2, '0')}`
  }

  function seekBy(v, delta) {
    v.currentTime = Math.max(0, Math.min(v.duration || 1e9, v.currentTime + delta))
    flash(`${delta > 0 ? '⏩ +' : '⏪ '}${delta}s   ${fmt(v.currentTime)}`)
  }

  function handler(e) {
    const v = getVideo()
    if (!v) return

    // don't hijack typing in inputs
    const t = e.target
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
    if (e.ctrlKey || e.altKey || e.metaKey) return

    const step = store.seekSeconds || 10
    const key = e.key

    switch (key) {
      case ' ':
      case 'k': case 'K':
        v.paused ? v.play() : v.pause()
        flash(v.paused ? '⏸ 暫停' : '▶ 播放')
        break
      case 'ArrowRight': seekBy(v, step); break
      case 'ArrowLeft':  seekBy(v, -step); break
      case 'l': case 'L': seekBy(v, 10); break
      case 'j': case 'J': seekBy(v, -10); break
      case 'ArrowUp':
        v.volume = Math.min(1, v.volume + 0.05)
        flash(`🔊 ${Math.round(v.volume * 100)}%`); break
      case 'ArrowDown':
        v.volume = Math.max(0, v.volume - 0.05)
        flash(`🔊 ${Math.round(v.volume * 100)}%`); break
      case 'm': case 'M':
        v.muted = !v.muted
        flash(v.muted ? '🔇 靜音' : '🔊 取消靜音'); break
      case 'f': case 'F':
        if (document.fullscreenElement) document.exitFullscreen()
        else v.requestFullscreen?.()
        break
      case 'Home': v.currentTime = 0; flash('⏮ 開頭'); break
      case 'End':  v.currentTime = v.duration || 0; flash('⏭ 結尾'); break
      case '<': case ',':
        v.playbackRate = Math.max(0.25, v.playbackRate - 0.25)
        flash(`⏯ ${v.playbackRate}×`); break
      case '>': case '.':
        v.playbackRate = Math.min(4, v.playbackRate + 0.25)
        flash(`⏯ ${v.playbackRate}×`); break
      case 'Escape':
        if (!document.fullscreenElement && onClose) onClose()
        return   // let default run for fullscreen exit
      default:
        if (key >= '0' && key <= '9' && v.duration) {
          v.currentTime = v.duration * (Number(key) / 10)
          flash(`${Number(key) * 10}%   ${fmt(v.currentTime)}`)
          break
        }
        return   // not one of ours → don't preventDefault
    }
    e.preventDefault()
    e.stopPropagation()
  }

  onMounted(() => document.addEventListener('keydown', handler, true))
  onBeforeUnmount(() => {
    document.removeEventListener('keydown', handler, true)
    clearTimeout(toastTimer)
  })

  return { toast }
}
