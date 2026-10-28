<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { usePlayerHotkeys, loadPlayerAudio, savePlayerAudio } from '../player'
import { createWatchGate } from '../videoflags'
import { store } from '../store'
import Icon from './Icon.vue'

const props = defineProps({
  src: { type: String, required: true },
  // A ready-to-use WebVTT URL, or null. The parent resolves it — this
  // component stays free of the API (see the note under "watched").
  subtitle: { type: String, default: null },
})
const emit = defineEmits(['close', 'watched'])

const videoEl = ref(null)
const wrapEl = ref(null)
const showHelp = ref(false)
const { toast } = usePlayerHotkeys(() => videoEl.value, () => emit('close'))

// Start muted (remembered preference), and persist any change the viewer makes.
function applyAudio() {
  const v = videoEl.value
  if (!v) return
  const a = loadPlayerAudio()
  v.volume = a.volume
  v.muted = a.muted
}

function onVolumeChange() {
  const v = videoEl.value
  if (v) savePlayerAudio(v.volume, v.muted)
}

// `default` on a <track> is only honoured when the element is parsed with the
// video, and here it arrives a moment later. Setting the mode by hand is what
// actually turns the subtitle on — and it has to run again whenever the track
// element is replaced, which is why it hangs off the element's own load event
// as well as the prop.
// Cue placement is left to the browser, and that is a decision, not an
// oversight — it was tried the other way and reverted.
//
// The complaint it would have fixed: Chrome reserves room for its own control
// bar when it lays out an auto-placed cue, so the same line is drawn 72px
// higher (12% of the video's height) while the controls are on screen. A cue
// is only laid out when it *appears*, so moving the mouse does not move the
// current subtitle — the next one just comes back somewhere else, which reads
// as the subtitle wandering.
//
// Pinning does fix that, and only in the percentage form: measured, `line: -1`
// with `snapToLines` still shifts with the controls, because Chrome shrinks the
// line grid too. But a percentage puts every cue at the SAME height, and this
// library's subtitles are bilingual — the Japanese line and its Chinese
// translation overlap in time, and the browser's automatic stacking is the only
// thing separating them. Pinned, they printed on top of each other:
// 「答えはそ答案 就在這裡こにある」.
//
// Hand-rolling the stacking (a percentage per overlap depth) breaks again the
// moment a line wraps to two rows, which is exactly the long bilingual cue this
// would need to get right. Not worth it for a jump.

function showSubs() {
  const v = videoEl.value
  if (!v || !props.subtitle) return
  for (const t of v.textTracks) t.mode = 'showing'
}

watch(() => props.subtitle, () => nextTick(showSubs))

// Move focus to the player so shortcuts work immediately — otherwise a text
// field the viewer touched earlier keeps focus and swallows the keys.
function grabFocus() {
  const el = wrapEl.value
  if (!el) return
  if (document.activeElement && document.activeElement !== document.body) {
    document.activeElement.blur?.()
  }
  el.focus({ preventScroll: true })
}

// ── "watched" ──────────────────────────────────────────────────────────────
// The player is the only component that knows whether the video is actually
// playing, so the gate lives here and the parent — which knows the path —
// hears about it once. Emitting rather than posting keeps this component free
// of the API, the way it already is of everything except playback.
let gate = null
let ticker = null

function newGate() {
  clearInterval(ticker)
  ticker = null
  gate = createWatchGate({ onReach: () => { emit('watched'); stopTicking() } })
}

function stopTicking() { clearInterval(ticker); ticker = null }

function onPlaying() {
  if (!gate || gate.done) return
  gate.play(Date.now())
  // A second is plenty: the threshold is five, so the mark lands within a
  // second of it being earned and nobody is watching a clock.
  if (!ticker) ticker = setInterval(() => gate?.tick(Date.now()), 1000)
}

function onPaused() {
  gate?.pause(Date.now())
  stopTicking()
}

// The clip dialog needs currentTime/duration and needs to seek — everything
// else keeps working through the element's own controls.
defineExpose({ el: videoEl })

onMounted(() => { applyAudio(); grabFocus(); newGate() })
watch(() => props.src, () => {
  applyAudio()
  grabFocus()
  // A different file is a different question; the clip dialog and the compare
  // view both swap the source without remounting.
  newGate()
})

// Just removing the element leaves the range request hanging, which keeps a
// file handle open on the server — Windows then refuses to delete the file
// ("正由另一個程序使用"). Tearing the source down makes the browser abort now.
onBeforeUnmount(() => {
  stopTicking()
  const v = videoEl.value
  if (!v) return
  try {
    v.pause()
    v.removeAttribute('src')
    v.load()
  } catch { /* element already gone */ }
})
</script>

<template>
  <div class="pwrap" ref="wrapEl" tabindex="-1">
    <!-- controlsList hides the download item in the browser's ⋮ menu;
         @contextmenu also blocks the right-click "save video as" entry.

         NO `playsinline`, and that is a decision — it was added and reverted,
         so do not "fix" it back.

         The reasoning that led to adding it is still true: without it, iOS
         hands playback to the system's own fullscreen player, which draws
         subtitles from the phone's Accessibility settings, so `::cue` never
         applies and the black box this app deliberately removed comes back.
         That is what an iPhone reported: "手機版字幕的底色與網頁版的不同".

         But the trade is not worth taking, because iOS returns to the system
         player the moment anyone taps fullscreen — and on a phone that is the
         normal way to watch. So the styling it buys is only visible while you
         are NOT fullscreen, while the cost is paid every time: tapping a video
         stopped enlarging it, which the user noticed immediately and did not
         want.

         VideoCompare keeps `playsinline` on purpose. Two videos side by side
         is the whole point there; going fullscreen would defeat it. -->
    <video
      ref="videoEl" :src="src" controls autoplay
      controlsList="nodownload noremoteplayback"
      disablePictureInPicture
      @contextmenu.prevent
      @volumechange="onVolumeChange"
      @loadedmetadata="applyAudio"
      @playing="onPlaying"
      @pause="onPaused"
      @ended="onPaused"
      @waiting="onPaused"
    >
      <!-- Only rendered once the parent has confirmed the subtitle exists: a
           <track> pointing at a 404 still gives the browser a subtitle button
           that does nothing. -->
      <track v-if="subtitle" kind="subtitles" label="字幕" default
             :src="subtitle" @load="showSubs" />
    </video>

    <!-- transient feedback, YouTube-style -->
    <transition name="fade">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </transition>

    <button class="help" @click="showHelp = !showHelp" title="快捷鍵"><Icon name="keyboard" :size="15" /></button>
    <div v-if="showHelp" class="helpbox" @click="showHelp = false">
      <div><b>空白 / K</b> 播放暫停</div>
      <div><b>← →</b> 快退/快進 {{ store.seekSeconds }} 秒</div>
      <div><b>J / L</b> ∓10 秒</div>
      <div><b>↑ ↓</b> 音量</div>
      <div><b>M</b> 靜音　<b>F</b> 全螢幕</div>
      <div><b>0-9</b> 跳至 0%-90%</div>
      <div><b>, .</b> 播放速度</div>
      <div><b>Esc</b> 關閉</div>
    </div>
  </div>
</template>

<style scoped>
.pwrap { position: relative; outline: none; }
video { width: 100%; max-height: 74vh; max-height: 74dvh; background: #000; border-radius: 6px; display: block; }

/* The browser draws its own control bar, and its default backdrop is a solid
   dark block that hides the bottom of the picture. Replace it with a gradient
   so the video shows through while the controls stay readable.
   ::-webkit-media-controls-* is non-standard and undocumented; if a future
   Chrome drops it the rule simply stops applying and we are back to the
   default bar, so nothing breaks. */
/* Subtitle styling. `::cue` accepts only a short list of properties — colour,
   background, text-shadow, the font shorthand (line-height included), opacity,
   outline — which happens to cover both of the complaints here.

   No background box at all: the browser's default is a solid black slab that
   covered a real part of the picture. The readability it provided comes back as
   a shadow drawn on the glyphs themselves, repeated so it is dense enough to
   survive a bright background (sky, snow) where a single soft shadow washes out.

   No line-height either, and that one was tried and reverted. Squeezing the box
   moves an auto-placed cue DOWN the screen faster than it closes the gap —
   measured on the same frame, 1.2 bought 9px of spacing and cost 25px of
   height, putting the subtitle on the very edge of the picture. The bilingual
   gap is fixed where it actually comes from instead: two cues with identical
   timing are merged server-side into one two-line cue (see subtitles.py), so
   the spacing becomes ordinary in-box line spacing. */
video::cue {
  background: transparent;
  text-shadow: 0 0 4px #000, 0 0 4px #000, 0 1px 2px #000,
               0 0 8px rgba(0, 0, 0, .9);
}

video::-webkit-media-controls-panel {
  background-image: linear-gradient(to top,
                    rgba(0, 0, 0, .62) 0%,
                    rgba(0, 0, 0, .28) 55%,
                    rgba(0, 0, 0, 0) 100%);
  background-color: transparent;
}
.toast {
  position: absolute; top: 14px; left: 14px;
  background: rgba(0,0,0,.75); color: #fff; padding: 6px 14px;
  border-radius: 6px; font-size: 14px; pointer-events: none;
}
.fade-enter-active, .fade-leave-active { transition: opacity .18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.help {
  position: absolute; top: 10px; right: 10px;
  background: rgba(0,0,0,.55); color: #fff; border: none;
  border-radius: 6px; padding: 4px 8px; font-size: 13px; cursor: pointer;
}
.helpbox {
  position: absolute; top: 42px; right: 10px;
  background: rgba(20,20,32,.95); border: 1px solid #444;
  border-radius: 8px; padding: 10px 14px; font-size: 12px; line-height: 1.8;
  color: var(--text); cursor: pointer;
}
.helpbox b { color: var(--accent); margin-right: 6px; }
</style>
