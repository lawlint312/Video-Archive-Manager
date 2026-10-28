<script setup>
/**
 * Two videos playing side by side, kept in step.
 *
 * A component rather than markup repeated in place, for a reason that bit:
 * a `ref` used inside `v-for` becomes an *array* in Vue 3, and two compare
 * areas on one page would otherwise fight over the same two refs. Each
 * instance owning its own pair of elements makes both problems go away.
 *
 * The synchronisation rules live in `src/syncplay.js` with their tests —
 * they are not obvious and they were arrived at by getting them wrong.
 */
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { echoGuard, driftAction } from '../syncplay'
import Icon from './Icon.vue'

const props = defineProps({
  leftSrc: { type: String, required: true },
  rightSrc: { type: String, required: true },
  leftLabel: { type: String, default: '原始' },
  rightLabel: { type: String, default: '之後' },
  // 'metadata' for full-length files (do not pull gigabytes), 'auto' for the
  // short preview clips, where buffering both up front is what makes starting
  // together look instant
  preload: { type: String, default: 'metadata' },
})

const left = ref(null)
const right = ref(null)
const syncOn = ref(true)
// Playing both at once means doubled audio, so exactly one side is audible.
// Audio gets re-encoded too, so which side you hear is a real choice.
const audioFrom = ref('right')

const { hold, held } = echoGuard()
const pair = el => (el === left.value ? right.value : left.value)

function onPlay(e) {
  if (!syncOn.value || held(e.target)) return
  const o = pair(e.target)
  if (o && o.paused) { hold(o); o.play().catch(() => {}) }
}
function onPause(e) {
  if (!syncOn.value || held(e.target)) return
  const o = pair(e.target)
  if (o && !o.paused) { hold(o); o.pause() }
}
function onSeek(e) {
  if (!syncOn.value || held(e.target)) return
  const o = pair(e.target)
  if (o) { hold(o); o.currentTime = e.target.currentTime }
}

// Rate echoes are spotted by value, not by time: drift correction changes the
// rate constantly, and a time guard would leave the follower permanently deaf
// to the user's own actions.
const ourRate = new WeakMap()
function setRate(el, r) {
  if (Math.abs(el.playbackRate - r) < 0.005) return
  ourRate.set(el, r)
  el.playbackRate = r
}
function onRate(e) {
  if (!syncOn.value) return
  if (Math.abs((ourRate.get(e.target) ?? -1) - e.target.playbackRate) < 0.005) return
  const o = pair(e.target)
  if (o) setRate(o, e.target.playbackRate)
}

function onTime(e) {
  if (!syncOn.value) return
  const a = e.target
  if (a !== left.value) return          // one driver, or they fight each other
  const b = right.value
  if (!b || a.paused || b.paused || held(b)) return
  const act = driftAction(a.currentTime - b.currentTime, a.playbackRate)
  if (act.kind === 'seek') {
    hold(b)
    b.currentTime = a.currentTime
    setRate(b, a.playbackRate)
  } else if (act.kind === 'rate') {
    setRate(b, act.rate)
  }
}

function together(fn) {
  const a = left.value, b = right.value
  if (!a || !b) return
  hold(a)
  hold(b)
  fn(a, b)
}
function bothPlay() {
  together((a, b) => {
    b.currentTime = a.currentTime
    applyAudio()
    a.play().catch(() => {})
    b.play().catch(() => {})
  })
}
function bothPause() { together((a, b) => { a.pause(); b.pause() }) }
function bothRestart() {
  together((a, b) => {
    a.currentTime = 0
    b.currentTime = 0
    applyAudio()
    a.play().catch(() => {})
    b.play().catch(() => {})
  })
}

// Both players to one moment, playing — the pending list uses it to take a
// concealed re-encode and its original straight to where the damage was.
function seekBoth(t) {
  together((a, b) => {
    a.currentTime = Math.max(0, t)
    b.currentTime = Math.max(0, t)
    applyAudio()
    a.play().catch(() => {})
    b.play().catch(() => {})
  })
}
defineExpose({ seekBoth })

function applyAudio() {
  if (left.value) left.value.muted = audioFrom.value !== 'left'
  if (right.value) right.value.muted = audioFrom.value !== 'right'
}
watch(audioFrom, applyAudio)
watch(() => [props.leftSrc, props.rightSrc], () => nextTick(applyAudio),
      { immediate: true })

// Leaving a <video> pointed at a live stream keeps the request open; tear it
// down so the server is not left serving something nobody is watching.
onBeforeUnmount(() => {
  for (const el of [left.value, right.value]) {
    try {
      el?.pause()
      el?.removeAttribute('src')
      el?.load()
    } catch { /* already gone */ }
  }
})
</script>

<template>
  <div class="cmp">
    <div class="syncbar">
      <button class="btn small ib" @click="bothPlay">
        <Icon name="play" :size="13" /> 一起播放</button>
      <button class="mini" @click="bothPause">暫停</button>
      <button class="mini" @click="bothRestart">從頭</button>
      <label class="ck ib"><input type="checkbox" v-model="syncOn" /> 同步</label>
      <span class="spacer"></span>
      <span class="lab">聲音</span>
      <button v-for="o in [['left', leftLabel], ['right', rightLabel], ['none','靜音']]"
              :key="o[0]" class="mini" :class="{ on: audioFrom === o[0] }"
              @click="audioFrom = o[0]">{{ o[1] }}</button>
    </div>

    <div class="pair">
      <div>
        <div class="cap">{{ leftLabel }}<slot name="left-meta"></slot></div>
        <video ref="left" :src="leftSrc" controls :preload="preload" playsinline
               @play="onPlay" @pause="onPause" @seeked="onSeek"
               @ratechange="onRate" @timeupdate="onTime"></video>
      </div>
      <div>
        <div class="cap">{{ rightLabel }}<slot name="right-meta"></slot></div>
        <video ref="right" :src="rightSrc" controls :preload="preload" playsinline
               @play="onPlay" @pause="onPause" @seeked="onSeek"
               @ratechange="onRate" @timeupdate="onTime"></video>
      </div>
    </div>

    <div class="hint">
      同步開著的時候，其中一邊播放／暫停／拖動，另一邊會跟上。跑久了難免有時間差，
      校正的方式是把落後那邊的<b>播放速度微調 5%</b> 慢慢追上，而不是直接跳 ——
      跳轉會讓解碼器卡一下，那正是「一直播一下停一下」的原因。
      想單獨細看某一邊就把「同步」取消。
    </div>
  </div>
</template>

<style scoped>
.syncbar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px;
  font-size: 11px; flex-wrap: wrap; }
.spacer { flex: 1; }
.lab { color: var(--dim); }
.ck { color: var(--dim); }
.btn.small { font-size: 11px; padding: 4px 10px; }
.mini { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 9px; cursor: pointer; }
.mini:hover { border-color: var(--accent); }
.mini.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
/* Two 180px videos side by side tell you nothing about picture quality, which
   is the entire purpose of this component — so on a phone they stack. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .pair { grid-template-columns: 1fr; }
  .pair video { max-height: 32vh; max-height: 32dvh; }
}
.pair video { width: 100%; max-height: 42vh; max-height: 42dvh; background: #000;
  border-radius: 4px; display: block; }
.cap { font-size: 11px; color: var(--dim); margin-bottom: 4px; }
.hint { font-size: 11px; color: var(--dim); line-height: 1.6; margin-top: 6px; }
.hint b { color: var(--text); }
</style>
