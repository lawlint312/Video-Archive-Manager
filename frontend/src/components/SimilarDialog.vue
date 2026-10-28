<script setup>
/**
 * The 相似 sort's settings: threshold, scan, apply. Edit mode only.
 *
 * Opens every time 相似 is clicked in edit mode (the user's call). The slider
 * previews immediately from cached hashes — grouping 4,392 photos takes about
 * half a second — so the only slow thing is 開始掃描 when photos have never
 * been hashed, and that is the only thing the progress bar and ETA are for.
 *
 * Closing mid-scan does not stop it: the hashes are cached, so finishing is
 * never wasted, and reopening the dialog finds the same job again.
 */
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { api, errText } from '../api'
import { pct, fmtProgressCount, fmtEta } from '../scanprogress'
import { thresholdHint, colorHint, THRESHOLD_GUIDE, COLOR_GUIDE } from '../photosort'
import Icon from './Icon.vue'

const props = defineProps({ folder: { type: String, required: true } })
const emit = defineEmits(['close', 'applied'])

const loading = ref(true)
const error = ref('')
const limits = ref({ min: 20, max: 90, default: 70,
                     color_min: 90, color_max: 98, color_default: 93 })
const threshold = ref(70)
// Layer 2: same outfit in another pose, by colour. On by default — it is what
// makes one outfit stop splitting into blocks (see core/photosimilar.py).
const colorOn = ref(true)
const colorValue = ref(93)
const stats = ref(null)          // last /api/profile/similar answer
const previewing = ref(false)

const jobId = ref(null)
const progress = ref(null)
const applying = ref(false)
let stopped = false
let pollTimer = null
let previewTimer = null

const scanning = computed(() => !!jobId.value)
const hint = computed(() => thresholdHint(threshold.value))
const cHint = computed(() => colorHint(colorValue.value))
const colorParam = computed(() => colorOn.value ? colorValue.value : 0)
const progPct = computed(() => pct(progress.value) ?? 0)
const etaText = computed(() => fmtEta(progress.value?.eta))

const url = (th, co) => `/api/profile/similar?folder=${encodeURIComponent(props.folder)}` +
  (th == null ? '' : `&threshold=${th}&color=${co}`)

async function load(th, co) {
  const d = await api(url(th, co))
  stats.value = d
  return d
}

onMounted(async () => {
  try {
    const d = await load(null)
    limits.value = { ...limits.value, ...(d.limits || {}) }
    threshold.value = d.threshold
    // A folder applied before the colour layer existed has saved.color null:
    // offer the layer switched on, so applying again turns it on. 0 is an
    // editor having switched it off, and stays off.
    const sc = d.saved ? d.saved.color : undefined
    colorOn.value = sc === undefined || sc === null || sc > 0
    colorValue.value = sc > 0 ? sc : limits.value.color_default
    if (colorOn.value && sc !== d.color) await load(threshold.value, colorParam.value)
    if (d.running) attach(d.running)
  } catch (e) {
    error.value = '讀取失敗：' + errText(e)
  } finally {
    loading.value = false
  }
})

// Live preview while dragging. Debounced: each answer costs ~0.75 s of server
// time on a big folder, and only where the slider comes to rest matters.
watch([threshold, colorParam], ([th, co]) => {
  if (loading.value) return
  clearTimeout(previewTimer)
  previewTimer = setTimeout(async () => {
    previewing.value = true
    try { await load(th, co) } catch (e) { error.value = errText(e) }
    finally { previewing.value = false }
  }, 300)
})

function attach(id) {
  jobId.value = id
  poll()
}

async function poll() {
  if (stopped || !jobId.value) return
  try {
    const j = await api(`/api/profile/similar/job/${jobId.value}`)
    progress.value = typeof j.progress === 'object' ? j.progress : null
    if (j.status === 'running') {
      pollTimer = setTimeout(poll, 700)
      return
    }
    jobId.value = null
    if (j.status === 'error') { error.value = '掃描失敗：' + (j.error || ''); return }
    await apply()
  } catch (e) {
    jobId.value = null
    error.value = '掃描失敗：' + errText(e)
  }
}

async function start() {
  error.value = ''
  if (stats.value && stats.value.missing === 0) { await apply(); return }
  try {
    const { job_id } = await api('/api/profile/similar/scan', {
      method: 'POST', body: JSON.stringify({ folder: props.folder }),
    })
    progress.value = null
    attach(job_id)
  } catch (e) {
    error.value = '無法開始掃描：' + errText(e)
  }
}

/** Store the threshold for this folder, then hand the groups to the grid. */
async function apply() {
  applying.value = true
  try {
    const th = threshold.value, co = colorParam.value
    await api('/api/profile/similar/apply', {
      method: 'POST',
      body: JSON.stringify({ folder: props.folder, threshold: th, color: co }),
    })
    const d = await load(th, co)
    emit('applied', { threshold: d.threshold, groups: d.groups })
  } catch (e) {
    error.value = '套用失敗：' + errText(e)
  } finally {
    applying.value = false
  }
}

onBeforeUnmount(() => {
  stopped = true
  clearTimeout(pollTimer)
  clearTimeout(previewTimer)
})
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="image" :size="15" /> 相似排序</span>
        <span class="spacer"></span>
        <button class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 關閉
        </button>
      </div>

      <div v-if="loading" class="dim ib"><Icon name="spinner" :size="13" class="spin" /> 讀取中…</div>
      <template v-else-if="stats">
        <p class="intro">
          把相似的照片排在一起，其餘照片照時間順序。只改顯示順序，不會更動任何檔案。
        </p>

        <div class="guide">{{ THRESHOLD_GUIDE }}</div>
        <div class="row">
          <label class="lab">構圖門檻</label>
          <input type="range" :min="limits.min" :max="limits.max" step="1"
                 v-model.number="threshold" :disabled="scanning || applying" />
          <span class="val">{{ threshold }} / 256</span>
        </div>
        <div class="hint" :class="hint.level">{{ hint.text }}</div>

        <label class="ib chk">
          <input type="checkbox" v-model="colorOn" :disabled="scanning || applying" />
          同一套造型換了姿勢也排在一起（比對顏色）
        </label>
        <template v-if="colorOn">
          <div class="guide">{{ COLOR_GUIDE }}</div>
          <div class="row">
            <label class="lab">顏色門檻</label>
            <input type="range" :min="limits.color_min" :max="limits.color_max" step="1"
                   v-model.number="colorValue" :disabled="scanning || applying" />
            <span class="val">{{ colorValue }}%</span>
          </div>
          <div class="hint" :class="cHint.level">{{ cHint.text }}</div>
        </template>

        <div class="preview">
          <template v-if="stats.cached">
            <Icon v-if="previewing" name="spinner" :size="12" class="spin" />
            目前設定：分成 <b>{{ stats.group_count }}</b> 組，共 <b>{{ stats.grouped }}</b> 張照片排在一起
            <span class="dim">（已掃描 {{ stats.cached }} 張）</span>
          </template>
          <template v-else>還沒掃描過，按「開始掃描」之後才看得到分組結果</template>
          <div v-if="stats.missing" class="dim">
            還有 {{ stats.missing }} 張照片沒掃描過，第一次每張約 30 毫秒，之後會記住
          </div>
        </div>

        <div v-if="scanning" class="prog">
          <div class="bar"><i :style="{ width: progPct + '%' }"></i></div>
          <div class="ptext">
            讀取照片 {{ fmtProgressCount(progress) }}
            <span v-if="etaText"> · 剩餘 {{ etaText }}</span>
          </div>
          <div class="dim">可以關閉視窗，掃描會在背景繼續</div>
        </div>

        <div v-if="error" class="err">{{ error }}</div>

        <div class="foot">
          <span class="spacer"></span>
          <button class="btn ib" :disabled="scanning || applying" @click="start">
            <Icon :name="applying ? 'spinner' : 'search'" :size="13" :class="{ spin: applying }" />
            {{ stats.missing ? '開始掃描' : '套用' }}
          </button>
        </div>
      </template>
      <div v-else-if="error" class="err">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border-radius: 10px; padding: 14px 16px;
  width: min(520px, 94vw); display: flex; flex-direction: column; gap: 10px;
  color: var(--text); font-size: 13px; }
.dhead { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.spacer { flex: 1; }
.intro { margin: 0; color: var(--dim); line-height: 1.6; font-size: 12px; }
.guide { font-size: 12px; line-height: 1.6; color: var(--text); margin-top: 6px; }
.row { display: flex; align-items: center; gap: 10px; }
.row input[type=range] { flex: 1; }
.lab { white-space: nowrap; min-width: 56px; }
.chk { gap: 6px; cursor: pointer; font-size: 12px; margin-top: 4px; }
.val { font-variant-numeric: tabular-nums; min-width: 64px; text-align: right; }
.hint { font-size: 12px; }
.hint.safe { color: #6cc28a; }
.hint.ok { color: var(--accent); }
.hint.risky { color: #e9a24a; }
.preview { background: var(--card); border-radius: 6px; padding: 8px 10px;
  font-size: 12px; line-height: 1.7; }
.dim { color: var(--dim); font-size: 12px; }
.prog .bar { height: 6px; background: var(--card); border-radius: 3px; overflow: hidden; }
.prog .bar i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
.ptext { font-size: 12px; margin-top: 4px; font-variant-numeric: tabular-nums; }
.err { color: var(--danger, #e66); font-size: 12px; }
.foot { display: flex; align-items: center; gap: 8px; }
</style>
