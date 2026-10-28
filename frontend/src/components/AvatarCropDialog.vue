<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { avatarImgStyle, coverSizePx, clampFocal } from '../avatar'
import Icon from './Icon.vue'

const props = defineProps({
  src: { type: String, required: true },
  initial: { type: Object, default: () => ({ x: 50, y: 50, zoom: 1 }) },
})
const emit = defineEmits(['save', 'close'])

const PREVIEW = 300            // diameter of the editing circle

const x = ref(props.initial?.x ?? 50)
const y = ref(props.initial?.y ?? 50)
const zoom = ref(props.initial?.zoom ?? 1)
const natW = ref(0)
const natH = ref(0)

function onImgLoad(e) {
  natW.value = e.target.naturalWidth
  natH.value = e.target.naturalHeight
  applyClamp()
}

const imgStyle = computed(() =>
  avatarImgStyle(natW.value, natH.value,
                 { x: x.value, y: y.value, zoom: zoom.value }))

// how far the image can travel — used to tell the user which axes can pan
const slack = computed(() => {
  const { w, h } = coverSizePx(PREVIEW, natW.value, natH.value, zoom.value)
  return { x: Math.max(0, Math.round(w - PREVIEW)), y: Math.max(0, Math.round(h - PREVIEW)) }
})

function applyClamp() {
  const c = clampFocal(natW.value, natH.value,
                       { x: x.value, y: y.value, zoom: zoom.value })
  x.value = c.x; y.value = c.y; zoom.value = c.zoom
}

// ── drag to pan (both axes, as long as there is slack) ──────────────────────
let dragging = false, startX = 0, startY = 0, baseX = 50, baseY = 50

function onDown(e) {
  dragging = true
  startX = e.clientX; startY = e.clientY
  baseX = x.value; baseY = y.value
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

function onMove(e) {
  if (!dragging) return
  const { w, h } = coverSizePx(PREVIEW, natW.value, natH.value, zoom.value)
  // moving the pointer right shifts the image right → focal point moves left
  x.value = baseX - (e.clientX - startX) / w * 100
  y.value = baseY - (e.clientY - startY) / h * 100
  applyClamp()
}

function onUp() {
  dragging = false
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
}

function onWheel(e) {
  e.preventDefault()
  zoom.value = Math.max(1, Math.min(5, zoom.value * (e.deltaY < 0 ? 1.08 : 1 / 1.08)))
  applyClamp()
}

function onZoomInput() { applyClamp() }
function reset() { x.value = 50; y.value = 50; zoom.value = 1; applyClamp() }
function save() {
  emit('save', { x: +x.value.toFixed(1), y: +y.value.toFixed(1),
                 zoom: +zoom.value.toFixed(3) })
}

onBeforeUnmount(onUp)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span>調整大頭照</span>
        <button class="btn ghost" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div class="body">
        <div class="stage" :style="{ width: PREVIEW + 'px', height: PREVIEW + 'px' }"
             @pointerdown.prevent="onDown" @wheel="onWheel">
          <img :src="src" :style="imgStyle" draggable="false" @load="onImgLoad" />
        </div>

        <div class="hint">
          拖曳移動位置 · 滾輪或滑桿縮放
          <span v-if="!slack.x && !slack.y" class="note">（放大後才能移動）</span>
          <span v-else-if="!slack.x" class="note">（此圖目前只能上下移動，放大可左右移動）</span>
          <span v-else-if="!slack.y" class="note">（此圖目前只能左右移動，放大可上下移動）</span>
        </div>

        <div class="zoomrow">
          <span>縮放</span>
          <input type="range" min="1" max="5" step="0.02"
                 v-model.number="zoom" @input="onZoomInput" />
          <span class="zval">{{ zoom.toFixed(2) }}×</span>
        </div>
      </div>

      <div class="foot">
        <button class="btn ghost" @click="reset">重設</button>
        <span class="spacer"></span>
        <button class="btn" @click="save">確定</button>
        <button class="btn ghost" @click="emit('close')">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.85);
  display: flex; align-items: center; justify-content: center; z-index: 70; }
.dlg { background: var(--panel); border-radius: 10px; width: min(420px, 94vw); }
/* The 300px circle plus its controls is about 450px tall, which fits a phone
   held upright but not one turned sideways. Scrolling beats being cut off.
   Inert on desktop, where the dialog never approaches this height. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .dlg { max-height: 92vh; max-height: 92dvh; overflow-y: auto; }
}
.dhead { display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid #313148; font-weight: bold; }
.body { padding: 18px 16px; display: flex; flex-direction: column; align-items: center; }
.stage { position: relative; border-radius: 50%; overflow: hidden; background: #000;
  cursor: grab; touch-action: none; border: 3px solid var(--accent); }
.stage:active { cursor: grabbing; }
.stage img { display: block; user-select: none; }
.hint { color: var(--dim); font-size: 11px; margin-top: 12px; text-align: center; }
.note { color: #f9e2af; }
.zoomrow { display: flex; align-items: center; gap: 10px; margin-top: 14px;
  width: 100%; font-size: 12px; color: var(--dim); }
.zoomrow input { flex: 1; }
.zval { width: 46px; text-align: right; }
.foot { display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; border-top: 1px solid #313148; }
.spacer { flex: 1; }
</style>
