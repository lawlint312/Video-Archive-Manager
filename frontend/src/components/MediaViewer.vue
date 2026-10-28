<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { swipeOutcome, follow, fade } from '../swipenav'
import { screen } from '../mobile'
import { imageUrl, imageThumbUrl, streamUrl } from '../api'
import VideoPlayer from './VideoPlayer.vue'
import Icon from './Icon.vue'

const props = defineProps({
  items: { type: Array, required: true },      // [{ path, is_video }]
  start: { type: Number, default: 0 },
})
const emit = defineEmits(['close'])

const idx = ref(props.start)
const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)
const natW = ref(0)
const natH = ref(0)
const imgEl = ref(null)

const current = computed(() => props.items[idx.value] || null)
const isVideo = computed(() => !!current.value?.is_video)
const fileName = computed(() => {
  const p = current.value?.path || ''
  return p.split(/[\\/]/).pop()
})

// True while fingers are moving the picture (pinch, or a pan after one).
// Two things flickered during a pinch on an iPhone: the 60 ms transform
// transition restarted on every pointermove, so the picture kept snapping
// between the previous scale and the new one; and every new scale made Safari
// re-rasterise the full-size photo. While the gesture runs: no transition,
// and `will-change` keeps the picture on its own layer, scaled as a bitmap.
// Dropping it afterwards lets Safari redraw it sharp once, at the final scale.
const gesturing = ref(false)
// A double tap jumps a whole step, so it animates instead of snapping.
const easing = ref(false)

const imgStyle = computed(() => ({
  transform: `translate(${panX.value}px, ${panY.value}px) scale(${zoom.value})`,
  cursor: zoom.value > 1 ? (dragging ? 'grabbing' : 'grab') : 'zoom-in',
  ...(gesturing.value ? { transition: 'none', willChange: 'transform' } : {}),
  ...(easing.value ? { transition: 'transform .2s ease-out' } : {}),
}))

// ── navigation ─────────────────────────────────────────────────────────────
function go(delta) {
  const n = props.items.length
  if (!n) return
  idx.value = (idx.value + delta + n) % n
}
const next = () => go(1)
const prev = () => go(-1)

// ── zoom / pan ─────────────────────────────────────────────────────────────
function resetView() { zoom.value = 1; panX.value = 0; panY.value = 0 }

function zoomBy(f) {
  zoom.value = Math.max(1, Math.min(8, zoom.value * f))
  if (zoom.value === 1) { panX.value = 0; panY.value = 0 }
}

function onWheel(e) {
  if (isVideo.value) return
  e.preventDefault()
  zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15)
}

// double-click toggles between fit-to-window and 100% (native pixels)
function onDblClick() {
  if (isVideo.value || !imgEl.value) return
  // a phone's double tap is handled in onTouchUp; if the browser also
  // reports it as a dblclick, that must not undo it
  if (performance.now() - lastDoubleTapAt < 600) return
  if (zoom.value > 1) { resetView(); return }
  const shown = imgEl.value.getBoundingClientRect().width / zoom.value
  zoom.value = shown ? Math.max(1, Math.min(8, natW.value / shown)) : 2
}

let dragging = false, sx = 0, sy = 0, bx = 0, by = 0
function onDown(e) {
  if (isVideo.value || zoom.value <= 1) return
  dragging = true; sx = e.clientX; sy = e.clientY; bx = panX.value; by = panY.value
  if (e.pointerType === 'touch') gesturing.value = true   // cleared by endTouches
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
function onMove(e) {
  if (!dragging) return
  panX.value = bx + (e.clientX - sx)
  panY.value = by + (e.clientY - sy)
}
function onUp() {
  dragging = false
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
}

// ── swipe between items (narrow screens only) ──────────────────────────────
// Deliberately not CSS-gated like the hidden edit tools: a gesture is
// behaviour, not appearance, so the check has to happen in script.
// `screen.mobile` covers a phone on its side too (see ../mobile.js): this was
// gated on `max-width: 760px`, so turning the phone switched swiping off.
const narrow = computed(() => screen.mobile)
// sideways, the gesture runs left/right (the user's choice)
const axis = computed(() => screen.landscape ? 'x' : 'y')
const extent = () => (axis.value === 'x' ? window.innerWidth : window.innerHeight) || 800

const swipeY = ref(0)          // how far the current item has been dragged
const settling = ref(false)    // true while animating out or springing back

const stageStyle = computed(() => ({
  transform: axis.value === 'x' ? `translateX(${swipeY.value}px)`
                                 : `translateY(${swipeY.value}px)`,
  opacity: fade(swipeY.value, extent()),
  transition: settling.value ? 'transform .18s ease-out, opacity .18s ease-out'
                             : 'none',
}))

let sw = null                  // the gesture in progress
function swipeAvailable() {
  // zoomed in, the same drag pans the picture — that path owns the gesture
  return narrow.value && !settling.value && (isVideo.value || zoom.value <= 1)
}

function onStageDown(e) {
  if (e.pointerType === 'mouse') return
  trackTouch(e)
  if (pinch || touches.size > 1) return
  if (!swipeAvailable()) return
  sw = { id: e.pointerId, x: e.clientX, y: e.clientY, t: performance.now(), moved: false }
  window.addEventListener('pointermove', onStageMove, { passive: false })
  window.addEventListener('pointerup', onStageUp)
  window.addEventListener('pointercancel', onStageUp)
}

function onStageMove(e) {
  if (!sw || e.pointerId !== sw.id) return
  const dy = e.clientY - sw.y
  const dx = e.clientX - sw.x
  if (!sw.moved && Math.abs(dy) < 8 && Math.abs(dx) < 8) return
  const main = axis.value === 'x' ? dx : dy
  const cross = axis.value === 'x' ? dy : dx
  // Claim the gesture only once it is clearly along the axis, so a tap on
  // the video's controls still reaches them.
  if (!sw.moved) {
    if (Math.abs(main) < Math.abs(cross)) { endSwipe(); return }
    sw.moved = true
  }
  e.preventDefault()
  swipeY.value = follow(main, extent())
}

function onStageUp(e) {
  if (!sw || e.pointerId !== sw.id) return
  const g = { dx: e.clientX - sw.x, dy: e.clientY - sw.y,
              dt: performance.now() - sw.t,
              height: extent(), axis: axis.value }
  const moved = sw.moved
  endSwipe()
  if (!moved) return
  const outcome = swipeOutcome(g)
  if (outcome === 'cancel') { spring(0); return }
  // fly the current item out the way it was going, then swap underneath
  const h = extent()
  settling.value = true
  swipeY.value = outcome === 'next' ? -h : h
  setTimeout(() => {
    go(outcome === 'next' ? 1 : -1)
    settling.value = false        // land the new item instantly, no slide back
    swipeY.value = 0
  }, 170)
}

function spring(to) {
  settling.value = true
  swipeY.value = to
  setTimeout(() => { settling.value = false }, 190)
}

function endSwipe() {
  sw = null
  window.removeEventListener('pointermove', onStageMove)
  window.removeEventListener('pointerup', onStageUp)
  window.removeEventListener('pointercancel', onStageUp)
}

// ── two-finger zoom (phones) ───────────────────────────────────────────────
// This used to be the browser's own pinch (`touch-action: pinch-zoom`), and
// the swipe handler never knew it was happening: the first finger of a pinch
// is a perfectly good one-finger drag, so spreading two fingers along the
// swipe axis turned the page. The browser's zoom also scales the whole page
// rather than the picture, leaving `zoom` at 1 — so swipeAvailable() still
// said yes while the user was looking at a magnified photo. Owning the pinch
// puts it on the same `zoom`/`pan` the wheel and double-click already use,
// and a second finger now cancels any swipe in progress.
const touches = new Map()      // pointerId → { x, y }, touch pointers only
let pinch = null
let lastPinchAt = 0            // a pinch's last finger can land as a "tap"

function trackTouch(e) {
  touches.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (touches.size === 1) {
    tap = { x: e.clientX, y: e.clientY, t: performance.now(),
            onImg: onPicture(e.target), spoilt: false }
    window.addEventListener('pointermove', onTouchMove)
    window.addEventListener('pointerup', onTouchUp)
    window.addEventListener('pointercancel', onTouchUp)
  }
  else if (tap) tap.spoilt = true        // a second finger: not a tap
  if (touches.size === 2 && !isVideo.value && imgEl.value) startPinch()
}

// the full image, or its thumbnail stand-in while the original downloads
function onPicture(el) {
  return !!imgEl.value && (el === imgEl.value || !!el?.classList?.contains('ph'))
}

function twoPoints() {
  const [a, b] = [...touches.values()]
  return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1,
           mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }
}

function startPinch() {
  // whatever the first finger had started gives way
  // Snap back at once rather than spring(0): an 0.18 s slide-and-fade at the
  // very moment the zoom starts read as a flash.
  endSwipe()
  settling.value = false
  swipeY.value = 0
  onUp()
  gesturing.value = true
  const r = imgEl.value.getBoundingClientRect()
  const p = twoPoints()
  // The image's untransformed centre: scale() pivots on it, translate()
  // shifts it, so subtracting the current pan recovers it.
  const cx = r.left + r.width / 2 - panX.value
  const cy = r.top + r.height / 2 - panY.value
  // the point of the picture under the fingers, in unscaled pixels
  pinch = { d0: p.d, z0: zoom.value, cx, cy,
            ux: (p.mx - cx - panX.value) / zoom.value,
            uy: (p.my - cy - panY.value) / zoom.value }
}

function onTouchMove(e) {
  if (!touches.has(e.pointerId)) return
  touches.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (tap && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) > TAP_SLOP) tap.spoilt = true
  if (!pinch || touches.size < 2) return
  const p = twoPoints()
  const z = Math.max(1, Math.min(8, pinch.z0 * p.d / pinch.d0))
  zoom.value = z
  // keep the point that was under the fingers under the fingers
  panX.value = p.mx - pinch.cx - pinch.ux * z
  panY.value = p.my - pinch.cy - pinch.uy * z
}

function onTouchUp(e) {
  touches.delete(e.pointerId)
  if (!touches.size && tap) onTap(tap)
  if (pinch && touches.size < 2) {
    pinch = null
    lastPinchAt = performance.now()
    if (zoom.value < 1.05) resetView()
    else if (touches.size === 1) {
      // the finger still down carries on as a pan, not a jump
      const [t] = [...touches.values()]
      dragging = true; sx = t.x; sy = t.y; bx = panX.value; by = panY.value
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    }
  }
  if (!touches.size) endTouches()
}

// ── double tap (phones): fit ↔ 200 % ───────────────────────────────────────
// The desktop's dblclick never arrives here: with `touch-action: none` on the
// stage the browser no longer synthesises it from taps. So taps are counted by
// hand: two on the picture within DOUBLE_TAP_MS and TAP_SLOP of each other,
// neither of them moving (a swipe or a pan is not a tap). Only on the picture
// (the user's choice): the black around it closes on a single tap, and
// counting double taps there would make every close wait 300 ms to be sure.
const DOUBLE_TAP_MS = 300
const TAP_SLOP = 30
const TAP_ZOOM = 2
let tap = null                 // the touch in progress, while it may be a tap
let lastTap = null             // the previous completed tap
let lastDoubleTapAt = 0

function onTap(t) {
  tap = null
  const now = performance.now()
  if (t.spoilt || !t.onImg || now - t.t > DOUBLE_TAP_MS) { lastTap = null; return }
  if (lastTap && now - lastTap.t < DOUBLE_TAP_MS
      && Math.hypot(t.x - lastTap.x, t.y - lastTap.y) < TAP_SLOP) {
    lastTap = null
    lastDoubleTapAt = now
    doubleTap(t.x, t.y)
    return
  }
  lastTap = { x: t.x, y: t.y, t: now }
}

function doubleTap(x, y) {
  if (isVideo.value || !imgEl.value) return
  easing.value = true
  setTimeout(() => { easing.value = false }, 220)
  // zoomed in any amount (a pinch too) → back to fit; otherwise 200 %
  if (zoom.value > 1) { resetView(); return }
  // Centre on the tapped point: at zoom 1 with no pan, the picture point
  // under the finger sits (x - cx) from the centre; after scaling by z it
  // would sit z times that far, so pan back by the difference.
  const r = imgEl.value.getBoundingClientRect()
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2
  zoom.value = TAP_ZOOM
  panX.value = (cx - x) * (TAP_ZOOM - 1)
  panY.value = (cy - y) * (TAP_ZOOM - 1)
}

function endTouches() {
  touches.clear()
  tap = null
  pinch = null
  gesturing.value = false
  window.removeEventListener('pointermove', onTouchMove)
  window.removeEventListener('pointerup', onTouchUp)
  window.removeEventListener('pointercancel', onTouchUp)
}

function closeFromBackdrop() {
  if (performance.now() - lastPinchAt < 400) return
  emit('close')
}

// iOS Safari zooms the page on a pinch whatever `touch-action` says unless
// its own gesture events are cancelled.
const stopGesture = (e) => e.preventDefault()

// ── loading state ──────────────────────────────────────────────────────────
// A full-size photo can be 10+ MB (3024×4032 phone originals), and the stage
// stayed black until it arrived — no sign anything was happening. The grid's
// thumbnail is already in the browser cache, so it goes up at once as a
// stand-in, with a "loading" pill that only appears if the wait is real.
const loaded = ref(false)
const failed = ref(false)
watch(() => current.value?.path, () => { loaded.value = false; failed.value = false })

function onImgLoad(e) {
  natW.value = e.target.naturalWidth
  natH.value = e.target.naturalHeight
  loaded.value = true
}
function onImgError() { failed.value = true }

// ── preload neighbours so ← / → feels instant ──────────────────────────────
function preload(i) {
  const it = props.items[i]
  if (!it || it.is_video) return
  const im = new Image()
  im.src = imageUrl(it.path)
}
function preloadAround() {
  const n = props.items.length
  if (!n) return
  preload((idx.value + 1) % n)
  preload((idx.value - 1 + n) % n)
}

watch(idx, () => { resetView(); nextTick(preloadAround) })

// ── keyboard ───────────────────────────────────────────────────────────────
// For videos the arrow keys belong to the player (seeking); VideoPlayer's own
// capture listener runs first and stops them, so [ ] / PageUp / PageDown are
// the always-available navigation keys.
function onKey(e) {
  const t = e.target
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
  if (e.ctrlKey || e.altKey || e.metaKey) return
  switch (e.key) {
    case 'Escape': emit('close'); break
    case 'ArrowRight': if (isVideo.value) return; next(); break
    case 'ArrowLeft':  if (isVideo.value) return; prev(); break
    case ']': case 'PageDown': next(); break
    case '[': case 'PageUp':   prev(); break
    case '+': case '=': if (isVideo.value) return; zoomBy(1.2); break
    case '-':           if (isVideo.value) return; zoomBy(1 / 1.2); break
    case '0':           if (isVideo.value) return; resetView(); break
    default: return
  }
  e.preventDefault()
  e.stopPropagation()
}

onMounted(() => {
  document.addEventListener('keydown', onKey, true)
  document.addEventListener('gesturestart', stopGesture, { passive: false })
  preloadAround()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey, true)
  document.removeEventListener('gesturestart', stopGesture)
  onUp()
  endSwipe()
  endTouches()
})
</script>

<template>
  <div class="viewer" @click.self="closeFromBackdrop">
    <!-- top bar -->
    <div class="bar">
      <span class="name" :title="current?.path">{{ fileName }}</span>
      <span class="count">{{ idx + 1 }} / {{ items.length }}</span>
      <span v-if="!isVideo && zoom > 1" class="zoom">{{ Math.round(zoom * 100) }}%</span>
      <button class="btn ghost" @click="emit('close')"><Icon name="close" :size="15" /></button>
    </div>

    <!-- prev / next -->
    <button class="nav left no-mobile" @click.stop="prev" title="上一個（←）"><Icon name="chevronLeft" :size="30" /></button>
    <button class="nav right no-mobile" @click.stop="next" title="下一個（→）"><Icon name="chevronRight" :size="30" /></button>

    <!-- stage -->
    <div class="stage" @click.self="closeFromBackdrop" @wheel="onWheel"
         :style="stageStyle" @pointerdown="onStageDown">
      <VideoPlayer v-if="isVideo" :key="current.path"
                   :src="streamUrl(current.path)" @close="emit('close')" />
      <template v-else-if="current">
        <!-- The stand-in takes the same transform and the same handlers as
             the full image. It used to sit still while a pinch scaled the
             half-downloaded original on top of it — a zoomed partial picture
             over an unzoomed thumbnail — and a tap on it counted as the
             backdrop, so double-tap did nothing and a single tap closed. -->
        <img v-if="!loaded && !failed" class="ph" :key="'ph:' + current.path"
             :src="imageThumbUrl(current.path)" :style="imgStyle" alt=""
             draggable="false" @pointerdown.prevent="onDown"
             @dblclick="onDblClick" />
        <img ref="imgEl" :key="current.path" class="full"
             :src="imageUrl(current.path)" :style="imgStyle" draggable="false"
             @load="onImgLoad" @error="onImgError"
             @pointerdown.prevent="onDown" @dblclick="onDblClick" />
        <div v-if="failed" class="loading err">無法載入這張照片</div>
        <div v-else-if="!loaded" class="loading"><span class="spin"></span>載入原圖中…</div>
      </template>
    </div>

    <div class="hint">
      <template v-if="narrow">{{ axis === 'x' ? '左右' : '上下' }}滑動換上下一個　·　雙指縮放、點兩下放大　·　點空白處關閉</template>
      <template v-else-if="isVideo">
        影片：← → 快轉／倒轉　·　<b>[</b> <b>]</b> 上下個媒體　·　Esc 關閉
      </template>
      <template v-else>
        ← → 上下張　·　滾輪縮放　·　雙擊 100%　·　拖曳移動　·　Esc 關閉
      </template>
    </div>
  </div>
</template>

<style scoped>
.viewer { position: fixed; inset: 0; background: rgba(0,0,0,.92); z-index: 80;
  display: flex; flex-direction: column; }
.bar { display: flex; align-items: center; gap: 14px; padding: 10px 14px;
  color: var(--text); font-size: 12px; }
.name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.count, .zoom { color: var(--dim); white-space: nowrap; }
.stage { flex: 1; display: flex; align-items: center; justify-content: center;
  overflow: hidden; min-height: 0; padding: 0 60px; }
.stage img { max-width: 100%; max-height: 100%; object-fit: contain;
  user-select: none; transition: transform .06s linear; }
.stage :deep(.pwrap) { width: min(1100px, 100%); }
.stage { position: relative; }
.stage img.full { position: relative; z-index: 1; }
/* Same box as the full image once it arrives (same aspect, `contain` inside
   the same padded area), so the swap is a sharpening, not a jump. */
.stage img.ph { position: absolute; top: 0; bottom: 0; left: 60px; right: 60px;
  width: calc(100% - 120px); height: 100%; max-width: none; max-height: none;
  object-fit: contain; }
.loading { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
  z-index: 2; display: flex; align-items: center; gap: 8px; padding: 6px 12px;
  border-radius: 999px; background: rgba(0,0,0,.65); color: #fff; font-size: 12px;
  pointer-events: none; white-space: nowrap;
  /* only if the wait is real: a cached photo must not flash it */
  animation: appear .2s ease .3s both; }
.loading.err { color: #f99; animation: none; }
.spin { width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
  animation: turn .8s linear infinite; }
@keyframes appear { from { opacity: 0 } to { opacity: 1 } }
@keyframes turn { to { transform: rotate(360deg) } }
.nav { position: fixed; top: 50%; transform: translateY(-50%);
  background: rgba(0,0,0,.45); color: #fff; border: none; font-size: 34px;
  width: 46px; height: 84px; cursor: pointer; z-index: 2; }
.nav:hover { background: rgba(0,0,0,.75); }
.nav.left { left: 6px; border-radius: 0 8px 8px 0; }
.nav.right { right: 6px; border-radius: 8px 0 0 8px; }
.hint { text-align: center; color: var(--dim); font-size: 11px; padding: 8px 0 12px; }

@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  /* The 60px gutters exist to keep the picture clear of the ← → buttons, and
     those are hidden on a phone — so they were costing 120px of a 375px
     screen for nothing. */
  .stage { padding: 0; }
  .stage img.ph { left: 0; right: 0; width: 100%; }
  /* Swiping down used to trigger Chrome's pull-to-refresh, reloading the page
     mid-browse. Calling preventDefault() in pointermove is too late: the
     browser decides whether to claim a touch gesture from `touch-action`
     before it ever dispatches the event.
     This was `pinch-zoom` for a while, to keep the browser's two-finger zoom
     (`none` had silently removed it). But the browser's pinch was invisible
     to the swipe handler, so a pinch also turned the page. The viewer now
     does its own pinch (see startPinch), which makes `none` safe again. */
  .stage { touch-action: none; }
  .viewer { overscroll-behavior: none; }
  .bar { padding: 8px 10px; gap: 10px; }
  .hint { padding: 6px 0 8px; }
}
.hint b { color: var(--text); }
</style>
