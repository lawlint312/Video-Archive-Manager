<script setup>
/**
 * Contact-sheet viewer: N frames per image, sampled at a fixed interval, so a
 * video can be judged without opening it.
 *
 * Sheets are built on the server one at a time (measured at ~2.5 s each for a
 * 1080p film), and asking for one starts the rest building in the background,
 * so paging is usually instant after the first.
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { api, errText } from '../api'
import { pct, isComplete, label as progLabel, createEta } from '../sheetprogress'
import { cellAt, cellTime, framesOn, canAdd, sortSegments, containBox, cellBox }
  from '../sheetcells'
import { store } from '../store'
import { fmtEta } from '../scanprogress'
import Icon from './Icon.vue'
import { swipeOutcome } from '../swipenav'
import { screen } from '../mobile'

const props = defineProps({
  video: { type: Object, required: true },   // { path, name }
  // The parent's player is open on top (from 「從這裡播放」): keys are its.
  paused: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'clip', 'play'])

const info = ref(null)
const index = ref(0)
const loading = ref(true)
const error = ref('')
// sheets already fetched once — a revisit should not show a spinner
const seen = ref(new Set())

const total = computed(() => info.value?.total || 0)
// `v` is the file's mtime, for the browser only: sheets are cached for 30 days
// under their URL, and a video replaced in place (a convert, a repair, cutting
// out a recording's blanks) keeps its path. The server drops the old sheets;
// without this the browser would keep showing them.
const src = computed(() =>
  `/api/sheet?path=${encodeURIComponent(props.video.path)}&index=${index.value}` +
  (props.video.mtime ? `&v=${Math.floor(props.video.mtime)}` : ''))

function hms(s) {
  s = Math.max(0, Math.floor(s))
  const p = n => String(n).padStart(2, '0')
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s % 3600 / 60))}:${p(s % 60)}`
}

const span = computed(() => {
  if (!info.value) return ''
  const from = index.value * info.value.span
  const to = Math.min(from + info.value.span, info.value.duration)
  return `${hms(from)} – ${hms(to)}`
})

function go(d) {
  const n = index.value + d
  if (n < 0 || n >= total.value) return
  index.value = n
  loading.value = !seen.value.has(n)
}

function onLoad(e) {
  loading.value = false
  seen.value = new Set(seen.value).add(index.value)
  const el = e?.target
  if (el?.naturalWidth) natural.value = { w: el.naturalWidth, h: el.naturalHeight }
  nextTick(measure)
}

// Sheets are wider than the dialog, so "fit" downscales them and the extra
// resolution is wasted. One click swaps to native size; the pane then scrolls
// and can be dragged.
const zoom = ref(false)
const wrapEl = ref(null)

/**
 * Zoom in on the point that was clicked.
 *
 * Without this you land at the top-left of a 2580px sheet and have to hunt
 * for the frame you were actually looking at.
 */
async function toggleZoom(e) {
  const el = wrapEl.value
  if (!el) { zoom.value = !zoom.value; return }
  let fx = 0.5, fy = 0.5
  if (!zoom.value && e?.currentTarget) {
    const r = e.currentTarget.getBoundingClientRect()
    fx = (e.clientX - r.left) / r.width
    fy = (e.clientY - r.top) / r.height
  }
  zoom.value = !zoom.value
  await nextTick()
  if (zoom.value) {
    el.scrollLeft = fx * el.scrollWidth - el.clientWidth / 2
    el.scrollTop = fy * el.scrollHeight - el.clientHeight / 2
  }
}

// Drag to pan. Scrollbars work too, but on an image this size grabbing it is
// what everyone tries first.
const dragging = ref(false)
let dragFrom = null
// mouseup fires before click, so by the time the click handler runs dragFrom
// is already cleared. This flag survives that gap; without it, letting go
// after a drag counted as a click and zoomed straight back out.
let lastMoved = false

function onDown(e) {
  if (!zoom.value || e.button !== 0) return
  const el = wrapEl.value
  dragFrom = { x: e.clientX, y: e.clientY, l: el.scrollLeft, t: el.scrollTop,
               moved: false }
  dragging.value = true
  e.preventDefault()
}
function onMove(e) {
  if (!dragFrom) return
  const el = wrapEl.value
  const dx = e.clientX - dragFrom.x, dy = e.clientY - dragFrom.y
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragFrom.moved = true
  el.scrollLeft = dragFrom.l - dx
  el.scrollTop = dragFrom.t - dy
  measure()                      // the overlay has to travel with the image
}
function onUp() {
  lastMoved = !!dragFrom?.moved
  dragFrom = null
  dragging.value = false
}

/* ── picking clip points off the sheet ─────────────────────────────────

   Marking is bound to the global 剪輯模式 rather than a switch of its own:
   the mode already exists, the user is already in it when they came here to
   look for segments, and a second toggle would be a second thing to forget.
   Outside that mode a click still zooms, exactly as before.

   Zoom does not become unreachable in the mode -- the 原尺寸檢視 button and
   the Z key were already there, and reading a frame closely is *more* useful
   while choosing, not less.

   A cell is an instant, not a bucket: frame n was grabbed at n × interval.
   So the end of a segment is the clicked cell's own timestamp with nothing
   added -- at a 30 s interval the 8th cell is 00:03:30 and a segment ending
   there ends at 00:03:30. The user's rule, and it is the one that matches
   what the timestamp burnt into the frame says.  */

const marking = computed(() => !!store.clipMode)
const markIn = ref(null)       // seconds, or null
const markOut = ref(null)
const segments = ref([])
let nextId = 1

const imgEl = ref(null)
const natural = ref({ w: 0, h: 0 })

const gridInfo = computed(() => {
  const i = info.value
  if (!i || !natural.value.w) return null
  return { cols: i.cols || 4, rows: i.rows || 3, pad: i.pad ?? 4,
           width: natural.value.w, height: natural.value.h }
})

/** Cells on *this* sheet that actually hold a frame. */
const liveCells = computed(() => info.value
  ? framesOn(index.value, info.value.per_sheet, info.value.frames)
  : 0)

function timeOf(cell) {
  const i = info.value
  return cellTime(index.value, cell, i.per_sheet, i.interval)
}

/** Which cell on this sheet a timestamp falls on, or null if another sheet. */
function cellOf(t) {
  const i = info.value
  if (!i || t == null) return null
  const n = Math.round(t / i.interval) - index.value * i.per_sheet
  return n >= 0 && n < liveCells.value ? n : null
}

function onSheetClick(e) {
  const g = gridInfo.value
  const el = imgEl.value
  if (!g || !el) return false
  const r = el.getBoundingClientRect()
  const box = containBox(r.width, r.height, g.width, g.height)
  if (!box) return false
  const fx = (e.clientX - r.left - box.x) / box.w
  const fy = (e.clientY - r.top - box.y) / box.h
  const cell = cellAt(fx, fy, g)
  if (cell == null || cell >= liveCells.value) return false

  const t = timeOf(cell)
  // First click sets the start. A second click after it sets the end; a
  // second click *before* it means the user picked the wrong start, so it
  // becomes the new start rather than an invalid pair they have to undo.
  if (markIn.value == null || (markOut.value != null) || t <= markIn.value) {
    markIn.value = t
    markOut.value = null
  } else {
    markOut.value = t
  }
  return true
}

const canAddSeg = computed(() => canAdd(markIn.value, markOut.value))

function addSegment() {
  if (!canAddSeg.value) return
  segments.value = sortSegments([...segments.value,
    { id: nextId++, start: markIn.value, end: markOut.value }])
  markIn.value = null
  markOut.value = null
}

function removeSegment(id) {
  segments.value = segments.value.filter(s => s.id !== id)
}

function clearMarks() {
  markIn.value = null
  markOut.value = null
}

/** Hand the chosen ranges to the clip dialog for frame-level adjustment. */
function toClip() {
  const out = canAddSeg.value
    ? sortSegments([...segments.value,
                    { id: nextId++, start: markIn.value, end: markOut.value }])
    : segments.value
  if (!out.length) return
  emit('clip', out.map(s => ({ start: s.start, end: s.end })))
}

/** Marker rectangles, in percentages of the painted image. */
function boxFor(cell) {
  const g = gridInfo.value
  const b = cell == null ? null : cellBox(cell, g || {})
  if (!b) return null
  return { left: b.x / g.width * 100 + '%', top: b.y / g.height * 100 + '%',
           width: b.w / g.width * 100 + '%', height: b.h / g.height * 100 + '%' }
}

const overlay = computed(() => {
  if (!marking.value || !gridInfo.value) return []
  const out = []
  const inCell = cellOf(markIn.value)
  const outCell = cellOf(markOut.value)
  if (inCell != null) out.push({ k: 'in', label: '起', ...boxFor(inCell) })
  if (outCell != null) out.push({ k: 'out', label: '迄', ...boxFor(outCell) })
  for (const s of segments.value) {
    for (const [t, label] of [[s.start, '起'], [s.end, '迄']]) {
      const c = cellOf(t)
      if (c != null) out.push({ k: 'done', label, ...boxFor(c) })
    }
  }
  return out.filter(o => o.left)
})

/** The painted image's offset inside its element, so the overlay lines up. */
const overlayStyle = ref(null)
function measure() {
  const el = imgEl.value
  if (!el || !natural.value.w) { overlayStyle.value = null; return }
  const r = el.getBoundingClientRect()
  const w = wrapEl.value?.getBoundingClientRect()
  const box = containBox(r.width, r.height, natural.value.w, natural.value.h)
  if (!box || !w) { overlayStyle.value = null; return }
  overlayStyle.value = {
    left: (r.left - w.left + box.x) + 'px', top: (r.top - w.top + box.y) + 'px',
    width: box.w + 'px', height: box.h + 'px',
  }
}

// ── swipe to turn the page (touch only, not while zoomed) ─────────────────
// The viewer had no gesture at all: on a phone the only way to the next sheet
// was the narrow arrow buttons. Left/right, the direction the arrows point,
// with the same thresholds as the photo viewer (../swipenav.js). Zoomed in,
// a drag pans the sheet instead, and the mouse keeps its own drag-to-pan path.
let tsw = null
let swipedAt = 0
function onTouchDown(e) {
  if (e.pointerType === 'mouse' || zoom.value) return
  tsw = { x: e.clientX, y: e.clientY, t: performance.now() }
}

/** The cell under a point, from the cells' own rectangles. */
function tileAt(x, y) {
  for (const el of wrapEl.value?.querySelectorAll('.tile') || []) {
    const r = el.getBoundingClientRect()
    if (x >= r.left && x < r.right && y >= r.top && y < r.bottom) return el
  }
  return null
}
function onTouchUp(e) {
  if (!tsw) return
  const g = { dx: e.clientX - tsw.x, dy: e.clientY - tsw.y,
              dt: performance.now() - tsw.t, axis: 'x',
              height: wrapEl.value?.clientWidth || window.innerWidth || 800 }
  tsw = null
  const o = swipeOutcome(g)
  if (o === 'cancel') {
    // A tap opens the cell from here rather than from a click, the way the
    // photo viewer counts its taps: one gesture path, and a swipe that ends
    // on a cell can never also open it.
    const tile = cellMode.value && tileAt(e.clientX, e.clientY)
    if (tile && Math.hypot(g.dx, g.dy) < 10 && g.dt < 600) {
      swipedAt = performance.now()            // swallow the click, if one comes
      focus.value = Number(tile.dataset.cell)
    }
    return
  }
  // a timestamp, not a flag: a touch drag may or may not be followed by a
  // click, and a flag left set would swallow the next genuine tap
  swipedAt = performance.now()
  go(o === 'next' ? 1 : -1)
}
function onTouchCancel() { tsw = null }

// While the viewer is up the page behind must not scroll: a drag on the
// overlay that has nothing of its own to scroll (a short film's single
// sheet) is otherwise handed to the video grid underneath.
const pageScroller = globalThis.document?.querySelector('main.content')
onMounted(() => pageScroller?.classList.add('scroll-locked'))
onBeforeUnmount(() => pageScroller?.classList.remove('scroll-locked'))

/** Click, unless the pointer was being dragged. */
function onClick(e) {
  if (performance.now() - swipedAt < 400) return
  if (lastMoved) { lastMoved = false; return }
  if (marking.value && onSheetClick(e)) return
  toggleZoom(e)
}

function onKey(e) {
  if (props.paused) return
  if (focus.value != null && e.key === 'Escape') { closeFocus(); return }
  if (e.key === 'ArrowRight' || e.key === 'PageDown') { go(1); e.preventDefault() }
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { go(-1); e.preventDefault() }
  else if (e.key === 'Escape') { if (zoom.value) zoom.value = false; else emit('close') }
  else if (e.key === 'z' || e.key === 'Z') toggleZoom()
}

/* ── how far along the whole set is ──────────────────────────────────────────
 * A sheet covers `interval × 12` seconds, so at the 30 s interval this library
 * uses the median film needs 20 sheets and the longest 30 — 30 to 50 seconds
 * of building behind a spinner that said only "產生中…".
 *
 * Polled rather than streamed: the numbers change a few times a second at
 * most, and a poll is a stat per sheet on the server (the duration is memoised
 * there, so no ffprobe). Polling stops the moment the set is complete.
 */
const POLL_MS = 700
const prog = ref(null)
const eta = createEta()
const etaSec = ref(null)
let pollTimer = null
// clearTimeout alone does not stop this loop: a poll that is already awaiting
// its response will schedule the next one after the viewer has closed, and
// keep the whole cycle alive forever.
let stopped = false

const progPct = computed(() => pct(prog.value))
const progText = computed(() => progLabel(prog.value))
const showProgress = computed(() =>
  !!prog.value && prog.value.total > 1 && !isComplete(prog.value))
const etaText = computed(() => fmtEta(etaSec.value))

async function pollProgress(forPath = props.video.path) {
  if (stopped) return
  try {
    const s = await api(`/api/sheet/status?path=${encodeURIComponent(forPath)}`)
    // the viewer may have moved to another video while this was in flight
    if (stopped || forPath !== props.video.path) return
    prog.value = s
    etaSec.value = eta.push(s)
    if (isComplete(s)) return            // done — stop polling entirely
  } catch {
    // a failed poll is not worth showing: the sheets themselves still work,
    // and the bar carrying on next tick is less alarming than an error
    if (stopped || forPath !== props.video.path) return
  }
  pollTimer = setTimeout(() => pollProgress(forPath), POLL_MS)
}

function restartPolling() {
  clearTimeout(pollTimer)
  eta.reset()
  etaSec.value = null
  prog.value = null
  pollProgress()
}

/* ── phone held upright: the sheet cut into its cells ────────────────────
   A sheet is 4 × 3 frames side by side, so on a 375px-wide phone each frame
   is about 90px across — the user had to turn the phone to read one. Upright,
   the same image (already fetched and cached) is cut into its cells with
   background-position and laid out 2 across, or 3 across when the frames are
   portrait (9:16 video — the user's choice after trying it). Nothing new is
   built on the server. Sideways the phone shows the whole sheet as before.

   A tapped cell opens on its own, full width, with 「從這裡播放」; left/right
   then steps cell by cell and carries on into the next sheet; a tap goes back
   to the grid at that cell. */
const cellMode = computed(() => screen.mobile && !screen.landscape)
// In cell mode the whole box scrolls, not the image pane (see the CSS).
const boxEl = ref(null)
const cellCols = computed(() => {
  const b = gridInfo.value && cellBox(0, gridInfo.value)
  return b && b.h > b.w ? 3 : 2
})
const cellList = computed(() => Array.from({ length: liveCells.value }, (_, i) => i))

function tileStyle(cell) {
  const g = gridInfo.value
  const b = g && cellBox(cell, g)
  if (!b) return {}
  // percentages, so the crop holds at whatever size the tile is drawn
  return {
    aspectRatio: `${b.w} / ${b.h}`, '--ar': b.w / b.h,
    backgroundImage: `url("${src.value}")`,
    backgroundSize: `${g.width / b.w * 100}% ${g.height / b.h * 100}%`,
    backgroundPosition: `${b.x / (g.width - b.w) * 100}% ${b.y / (g.height - b.h) * 100}%`,
  }
}

const focus = ref(null)        // the cell (on the current sheet) shown on its own
const focusTime = computed(() =>
  focus.value == null || !info.value ? null : timeOf(focus.value))
const focusOrdinal = computed(() =>
  focus.value == null ? 0 : index.value * info.value.per_sheet + focus.value + 1)

function openCell(c) {
  if (performance.now() - swipedAt < 400) return
  focus.value = c
}
function closeFocus() {
  const c = focus.value
  focus.value = null
  // back at the cell that was open, not wherever the grid had been scrolled
  nextTick(() => wrapEl.value?.querySelectorAll('.tile')[c]
    ?.scrollIntoView({ block: 'center' }))
}
function stepFocus(d) {
  const c = focus.value + d
  if (c >= 0 && c < liveCells.value) { focus.value = c; return }
  const n = index.value + d
  if (n < 0 || n >= total.value) return
  go(d)
  focus.value = d > 0 ? 0 : framesOn(n, info.value.per_sheet, info.value.frames) - 1
}

let fsw = null
function onFocusDown(e) {
  // the buttons keep their own click; only the picture area is a gesture
  if (e.target?.closest?.('button')) { fsw = null; return }
  fsw = { x: e.clientX, y: e.clientY, t: performance.now() }
}
function onFocusUp(e) {
  if (!fsw) return
  const g = { dx: e.clientX - fsw.x, dy: e.clientY - fsw.y,
              dt: performance.now() - fsw.t, axis: 'x',
              height: window.innerWidth || 375 }
  fsw = null
  const o = swipeOutcome(g)
  if (o === 'cancel') {
    // a tap goes back to the grid — decided here, not by a click (see onTouchUp)
    if (Math.hypot(g.dx, g.dy) < 10 && g.dt < 600 && e.pointerType !== 'mouse') {
      swipedAt = performance.now()
      closeFocus()
    }
    return
  }
  swipedAt = performance.now()
  stepFocus(o === 'next' ? 1 : -1)
}
function onFocusCancel() { fsw = null }
function onFocusClick() {
  if (performance.now() - swipedAt < 400) return
  closeFocus()
}

watch(cellMode, () => { focus.value = null })
// a new page starts at its first row
watch(index, () => {
  if (cellMode.value && focus.value == null && boxEl.value) boxEl.value.scrollTop = 0
})

// The overlay is positioned in pixels, so anything that can move the painted
// image has to re-measure: zooming, panning, paging, and the window changing
// size. Cheap enough to do unconditionally.
watch([zoom, index], () => nextTick(measure))
watch(segments, () => nextTick(measure))

watch(() => props.video.path, () => {
  markIn.value = null
  markOut.value = null
  segments.value = []
  index.value = 0
  seen.value = new Set()
  restartPolling()
})

onMounted(async () => {
  document.addEventListener('keydown', onKey)
  window.addEventListener('resize', measure)
  try {
    info.value = await api(
      `/api/sheet/info?path=${encodeURIComponent(props.video.path)}`)
    if (!info.value.total) error.value = '這個檔案讀不到時長，無法產生縮圖表'
    else pollProgress()
  } catch (e) {
    error.value = '無法讀取影片資訊：' + errText(e)
    loading.value = false
  }
})
// Closing the viewer stops the rest of the set being built. Prefetching is
// there to make paging instant while you are looking; once you are not, it is
// speculative work — up to twenty more sheets on a two-hour film — competing
// with whatever the machine does next. Fire-and-forget: if it fails, the
// prefetch simply finishes as it used to.
function stopPrefetch() {
  api(`/api/sheet/cancel?path=${encodeURIComponent(props.video.path)}`,
      { method: 'POST' }).catch(() => {})
}

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', measure)
  stopped = true
  clearTimeout(pollTimer)
  stopPrefetch()
})
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="box" ref="boxEl" :class="{ cellmode: cellMode }">
      <div class="head">
        <span class="nm" :title="video.name">{{ video.name }}</span>
        <span class="spacer"></span>
        <button class="btn ghost" title="關閉" @click="emit('close')">
          <Icon name="close" :size="14" /><span class="no-mobile">&nbsp;關閉</span>
        </button>
      </div>

      <div v-if="error" class="err">{{ error }}</div>

      <div v-else-if="info" class="stage"
           @pointerdown="onTouchDown" @pointerup="onTouchUp"
           @pointercancel="onTouchCancel">
        <button v-if="!cellMode" class="nav left" :disabled="index === 0" @click="go(-1)">
          <Icon name="chevronLeft" :size="22" />
        </button>
        <div class="imgwrap" ref="wrapEl" :class="{ zoom, dragging, cellmode: cellMode }"
             @mousedown="onDown" @mousemove="onMove"
             @mouseup="onUp" @mouseleave="onUp">
          <!-- In cell mode it stays, hidden, to load the sheet and report its
               size; the tiles below paint from the same cached image. -->
          <img :src="src" :class="{ zoom, hidden: cellMode }" draggable="false" ref="imgEl"
               @load="onLoad" @error="onLoad"
               :title="marking
                 ? '點一格設起點，再點後面一格設終點（放大用右下的按鈕或 Z）'
                 : (zoom ? '拖曳可移動，點一下縮回視窗大小' : '點一下放大該處')"
               @click="onClick" />
          <div v-if="marking && overlayStyle" class="cells" :style="overlayStyle">
            <div v-for="(o, i) in overlay" :key="i" class="cell" :class="o.k"
                 :style="{ left: o.left, top: o.top, width: o.width, height: o.height }">
              <span class="tag">{{ o.label }}</span>
            </div>
          </div>
          <div v-if="cellMode && gridInfo" class="tiles"
               :style="{ gridTemplateColumns: `repeat(${cellCols}, 1fr)` }">
            <!-- divs, not buttons; on touch a cell opens from onTouchUp -->
            <div v-for="c in cellList" :key="index + ':' + c" class="tile"
                 :data-cell="c" :style="tileStyle(c)" :title="hms(timeOf(c))"
                 @click="openCell(c)"></div>
          </div>
          <div v-if="loading" class="busy">
            <Icon name="spinner" :size="18" class="spin" /> 產生中…
          </div>
        </div>
        <button v-if="!cellMode" class="nav right" :disabled="index >= total - 1" @click="go(1)">
          <Icon name="chevronRight" :size="22" />
        </button>
      </div>

      <!-- Only in 剪輯模式. Outside it the viewer is exactly what it was. -->
      <div v-if="info && marking" class="pick">
        <span class="lead"><Icon name="scissors" :size="12" /> 點格子選取</span>
        <span class="mk" :class="{ set: markIn != null }">
          起 {{ markIn == null ? '—' : hms(markIn) }}</span>
        <span class="mk" :class="{ set: markOut != null }">
          迄 {{ markOut == null ? '—' : hms(markOut) }}</span>
        <button class="btn sm" :disabled="!canAddSeg" @click="addSegment">
          加入片段</button>
        <button class="lnk" v-if="markIn != null" @click="clearMarks">清除</button>
        <span class="spacer"></span>
        <span class="dim">每格 {{ info.interval }} 秒，之後可在剪輯視窗微調</span>
      </div>

      <div v-if="info && marking && segments.length" class="segs">
        <span v-for="s in segments" :key="s.id" class="seg">
          {{ hms(s.start) }} – {{ hms(s.end) }}
          <button class="x" title="移除" @click="removeSegment(s.id)">×</button>
        </span>
        <span class="spacer"></span>
        <button class="btn sm go" @click="toClip">
          <Icon name="scissors" :size="12" /> 送到剪輯微調（{{ segments.length }} 段）
        </button>
      </div>

      <div v-if="info" class="foot">
        <span>第 <b>{{ index + 1 }}</b> / {{ total }} 張</span>
        <span class="dim">· {{ span }}</span>
        <span class="dim">· 每 {{ info.interval }} 秒一格，{{ info.per_sheet }} 格</span>
        <span class="spacer"></span>
        <button v-if="!cellMode" class="lnk" @click="toggleZoom()">
          {{ zoom ? '縮回視窗大小' : '原尺寸檢視' }}
        </button>
        <span v-if="cellMode" class="dim">左右滑動翻頁 · 點格子放大</span>
        <span v-else-if="screen.mobile" class="dim">左右滑動翻頁 · 點一下放大</span>
        <span v-else class="dim">← → 翻頁 · Z 放大 · 放大後可拖曳 · Esc 關閉</span>
      </div>

      <!-- The whole set's progress, below the page counter. Hidden once every
           sheet exists, and for the one-sheet clips that make up 28% of this
           library — there is nothing to report about a set of one. -->
      <div v-if="showProgress" class="prog">
        <div class="bar"><i :style="{ width: progPct + '%' }"></i></div>
        <span class="ptext">{{ progText }}</span>
        <span v-if="etaText" class="dim">· 剩餘 {{ etaText }}</span>
      </div>
    </div>

    <!-- one cell on its own (phone, upright) -->
    <div v-if="cellMode && focus != null && gridInfo" class="focus"
         @pointerdown="onFocusDown" @pointerup="onFocusUp"
         @pointercancel="onFocusCancel" @click="onFocusClick">
      <div class="fhead">
        <button class="btn ghost" @click.stop="closeFocus">
          <Icon name="chevronLeft" :size="16" /> 返回</button>
        <span class="ftime">{{ hms(focusTime) }}</span>
        <span class="spacer"></span>
        <span class="dim">第 {{ focusOrdinal }} / {{ info.frames }} 格</span>
      </div>
      <div class="fimg">
        <div class="tile big" :style="tileStyle(focus)"></div>
        <div v-if="loading" class="busy">
          <Icon name="spinner" :size="18" class="spin" /> 產生中…
        </div>
      </div>
      <button class="btn playbtn" @click.stop="emit('play', focusTime)">
        <Icon name="play" :size="16" /> 從 {{ hms(focusTime) }} 播放</button>
      <div class="dim fhint">左右滑動換一格 · 點畫面返回</div>
    </div>
  </div>
</template>

<style scoped>
.cells { position: absolute; pointer-events: none; }
.cells .cell { position: absolute; border: 2px solid var(--accent, #4ea1ff);
  box-shadow: 0 0 0 1px rgba(0,0,0,.6) inset; border-radius: 2px; }
.cells .cell.out { border-color: #ffb648; }
.cells .cell.done { border-color: rgba(120, 230, 160, .85); border-style: dashed; }
.cells .tag { position: absolute; top: 2px; left: 2px; font-size: 11px;
  line-height: 1; padding: 2px 4px; border-radius: 2px; color: #fff;
  background: var(--accent, #4ea1ff); }
.cells .cell.out .tag { background: #ffb648; color: #201400; }
.cells .cell.done .tag { background: rgba(60, 140, 90, .9); }

.pick, .segs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 6px 10px; border-top: 1px solid var(--line, #2a2a36); font-size: 12px; }
.pick .lead { display: inline-flex; align-items: center; gap: 4px; opacity: .85; }
.pick .mk { padding: 2px 6px; border-radius: 3px; border: 1px dashed var(--line, #2a2a36);
  opacity: .6; }
.pick .mk.set { opacity: 1; border-style: solid; }
.segs .seg { display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px;
  border-radius: 3px; background: rgba(120, 230, 160, .14); }
.segs .seg .x { background: none; border: 0; color: inherit; cursor: pointer;
  font-size: 13px; line-height: 1; padding: 0 2px; opacity: .7; }
.segs .seg .x:hover { opacity: 1; }

.modal { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.box { display: flex; flex-direction: column; gap: 8px;
  width: min(1500px, 96vw); max-height: 94vh; max-height: 94dvh; }
.head { display: flex; align-items: center; gap: 10px; color: var(--text);
  font-size: 13px; }
.nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.spacer { flex: 1; }
/* The last row, under the page counter. Above the image it pushed the sheet
   down and then let it jump back up when the bar disappeared at 100%; down
   here the image never moves. `.box` is `max-height`, not a fixed height, so
   appearing and disappearing costs the image nothing. */
.prog { display: flex; align-items: center; gap: 9px; font-size: 12px;
  color: var(--text); }
.bar { flex: 1; height: 4px; border-radius: 2px; background: rgba(255,255,255,.14);
  overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--accent);
  border-radius: 2px; transition: width .3s linear; }
/* The count is the part people actually read, so it does not shrink and its
   digits do not shuffle as they change. */
.ptext { flex-shrink: 0; font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) { .bar i { transition: none; } }

.stage { display: flex; align-items: center; gap: 6px; min-height: 0; }
/* Fitted: a centred flex box. Zoomed: a plain block, because a centred flex
   child that overflows cannot be scrolled back to on the leading side — that
   is why the first version showed a fixed crop with no way out of it. */
.imgwrap { flex: 1; min-width: 0; position: relative; max-height: 82vh; max-height: 82dvh;
  overflow: auto; display: flex; align-items: center; justify-content: center; }
/* Fitted, a sideways drag belongs to the page-turn swipe, not to the browser;
   without this it may claim the gesture as a pan and cancel the pointer. */
.imgwrap:not(.zoom) { touch-action: pan-y pinch-zoom; }
.imgwrap.zoom { display: block; cursor: grab; }
.imgwrap.zoom.dragging { cursor: grabbing; }
.imgwrap img { max-width: 100%; max-height: 82vh; max-height: 82dvh; object-fit: contain;
  border-radius: 6px; cursor: zoom-in; user-select: none; }
.imgwrap img.zoom { max-width: none; max-height: none; display: block;
  cursor: inherit; }
.busy { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; gap: 8px; background: rgba(0,0,0,.55);
  color: #fff; font-size: 13px; border-radius: 6px; }
.nav { background: rgba(255,255,255,.08); border: none; color: #fff;
  border-radius: 8px; padding: 22px 6px; cursor: pointer; }
.nav:disabled { opacity: .25; cursor: default; }
.nav:not(:disabled):hover { background: rgba(255,255,255,.18); }
.foot { display: flex; align-items: center; gap: 8px; color: var(--text);
  font-size: 12px; }
.foot b { color: var(--accent); }
.dim { color: var(--dim); }
.err { color: #e88; font-size: 13px; padding: 20px; text-align: center; }
.lnk { background: none; border: none; color: var(--accent); font-size: 12px;
  cursor: pointer; }

/* phone upright: the sheet cut into cells (see cellMode) */
.imgwrap img.hidden { display: none; }
/* `cellmode`, not `cells`: `.cells` above is the clip-mark overlay and
   carries `pointer-events: none` + `position: absolute`. Named `cells`, the
   whole box took those — every touch fell through to the backdrop (which
   closes the viewer) or to the grid behind it. Looked like an iPhone bug for
   three rounds because the tests dispatched events straight at the cells
   instead of asking what is under the finger (elementFromPoint).
   One scroll for the whole box, with the title and the page line pinned. */
.imgwrap.cellmode { display: block; min-height: 40vh; overflow: visible; max-height: none; }
.box.cellmode { overflow-y: auto; overscroll-behavior: contain; }
.box.cellmode > * { flex-shrink: 0; }
.box.cellmode .stage { align-items: flex-start; touch-action: pan-y pinch-zoom; }
.box.cellmode .head { position: sticky; top: 0; z-index: 2; background: #0b0b12;
  padding: 2px 0; }
.box.cellmode .foot { position: sticky; bottom: 0; z-index: 2; background: #0b0b12;
  padding: 6px 0; }
.tiles { display: grid; gap: 4px; }
.tile { display: block; width: 100%; padding: 0; border: 0; border-radius: 4px;
  background-color: #12121a; background-repeat: no-repeat; cursor: pointer; }
/* z-index 3: above the box's sticky .head/.foot (2). At 1 the sheet's title
   and page line were drawn over this view — the page line right on top of
   the play button, which a tap then could not reach. */
.focus { position: fixed; inset: 0; z-index: 3; background: #000;
  display: flex; flex-direction: column; gap: 10px; padding: 10px 12px 16px;
  box-sizing: border-box; color: var(--text); touch-action: pan-y; }
.fhead { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.fhead .btn { min-height: 44px; }
.ftime { font-variant-numeric: tabular-nums; font-size: 15px; }
.fimg { flex: 1; min-height: 0; position: relative; display: flex;
  align-items: center; justify-content: center; }
/* contain: as wide as the screen, unless that would be taller than the room */
.tile.big { width: min(100%, calc((100dvh - 190px) * var(--ar, 1.78)));
  cursor: default; }
.playbtn { align-self: center; min-height: 44px; padding: 0 22px; font-size: 14px;
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--accent); border-color: var(--accent); color: #fff; }
.fhint { text-align: center; font-size: 11px; }

/* Phone (either way up): the box takes the whole screen, and 關閉 becomes a
   44px target set in from the corner, where the screen is rounded. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .box { width: 100vw; max-height: 100vh; max-height: 100dvh;
    padding: 10px 12px; box-sizing: border-box; }
  .head .btn { min-height: 44px; min-width: 44px; justify-content: center; }
  .imgwrap, .imgwrap img { max-height: calc(100dvh - 120px); }
  /* Too narrow for one line: let the pieces wrap as wholes rather than
     breaking 「第 1 / 2 張」 across two lines. */
  .foot { flex-wrap: wrap; row-gap: 2px; column-gap: 6px; }
  .foot > * { white-space: nowrap; }
  .foot .spacer { display: none; }
}
</style>
