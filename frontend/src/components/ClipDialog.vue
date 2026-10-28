<script setup>
/**
 * Cut segments out of a video. Several segments are joined into one file.
 *
 * The player is the whole point: nobody knows a highlight is at 10:02 before
 * watching, so in/out points are marked from playback (I and O, like every
 * editor) rather than typed. Marks are then added to a list — one segment or
 * six, the flow is the same, which is why there is no separate "single cut"
 * button to get wrong.
 *
 * The original is never touched. Removing it is a separate decision, made in
 * delete mode like every other deletion.
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api, streamUrl } from '../api'
import { setStatus } from '../store'
import VideoPlayer from './VideoPlayer.vue'
import { upsert, drop, commitable } from '../clipsegments'
import Icon from './Icon.vue'

const props = defineProps({
  video: { type: Object, required: true },
  // Ranges chosen on the contact sheet, if the user came that way. They are
  // coarse by construction — one cell is `sheet_interval` seconds — so they
  // arrive as ordinary segments to be adjusted here, with no special status.
  initial: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'created'])

const player = ref(null)
const inT = ref(null)
const outT = ref(null)
let nextId = 1
const segments = ref((props.initial || [])
  .map(s => ({ id: nextId++, start: s.start, end: s.end })))
const precise = ref(false)
const gpu = ref(false)
const plan = ref(null)
const planErr = ref('')
const running = ref(false)
const pct = ref(0)
const made = ref([])          // clips produced in this sitting

function el() { return player.value?.el || null }

function hms(s, decimals = 1) {
  if (s === null || s === undefined) return '—'
  s = Math.max(0, s)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const p = n => String(n).padStart(2, '0')
  const ss = decimals ? sec.toFixed(decimals).padStart(decimals + 3, '0')
                      : p(Math.floor(sec))
  return h ? `${h}:${p(m)}:${ss}` : `${p(m)}:${ss}`
}

function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(0) + ' MB'
}

const span = computed(() =>
  inT.value !== null && outT.value !== null ? outT.value - inT.value : null)
const canAdd = computed(() => commitable(inT.value, outT.value))
const total = computed(() =>
  segments.value.reduce((n, s) => n + (s.end - s.start), 0))

// ── marking ────────────────────────────────────────────────────────────────

function markIn() {
  const v = el(); if (!v) return
  inT.value = v.currentTime
  // an out point before the in point is meaningless — drop it rather than
  // leaving a pair that silently cannot be cut
  if (outT.value !== null && outT.value <= inT.value) outT.value = null
}
function markOut() {
  const v = el(); if (!v) return
  outT.value = v.currentTime
  if (inT.value !== null && outT.value <= inT.value) inT.value = null
}
function seekTo(t) {
  const v = el(); if (v && t !== null) v.currentTime = Math.max(0, t)
}
/** Fine adjustment. Refuses to push the pair below the 1 s minimum the server
 *  enforces — better to do nothing than to leave a selection that looks fine
 *  and is rejected on submit. */
function nudge(which, d) {
  const r = which === 'in' ? inT : outT
  if (r.value === null) return
  const next = Math.max(0, r.value + d)
  if (which === 'in' && outT.value !== null && next > outT.value - 1) return
  if (which === 'out' && inT.value !== null && next < inT.value + 1) return
  r.value = next
  seekTo(next)
}
/** Play the two seconds around the in point, so the cut can be judged. */
function preview() {
  const v = el(); if (!v || inT.value === null) return
  v.currentTime = Math.max(0, inT.value - 2)
  v.play?.()
}

/* ── editing a segment that is already in the list ──────────────────────

   The list used to be play-or-delete, which was survivable while every
   segment was marked here against the player a second earlier. It stopped
   being survivable when segments started arriving from the contact sheet:
   those are coarse by construction — one cell is `sheet_interval` seconds —
   and "adjust them here" was the entire point of handing them over.

   Editing reuses the marking row rather than growing a second set of
   controls: the segment goes back into the in/out marks, where seeking, I/O,
   the nudges and 預覽起點 all already work, and 加入片段 becomes 更新片段.
   The row stays in the list while this happens, marked as being edited —
   pulling it out would make a click look like a deletion.  */

const editingId = ref(null)
const editing = computed(() =>
  segments.value.find(s => s.id === editingId.value) || null)

function editSegment(s) {
  editingId.value = s.id
  inT.value = s.start
  outT.value = s.end
  seekTo(s.start)
}

function cancelEdit() {
  editingId.value = null
  inT.value = null
  outT.value = null
}

function addSegment() {
  if (!canAdd.value) return
  segments.value = upsert(segments.value, editingId.value,
                          inT.value, outT.value, nextId++)
  editingId.value = null
  inT.value = null
  outT.value = null
}
function dropSegment(id) {
  if (editingId.value === id) cancelEdit()
  segments.value = drop(segments.value, id)
}
function playSegment(s) {
  const v = el(); if (!v) return
  v.currentTime = s.start
  v.play?.()
}

// ── plan preview ───────────────────────────────────────────────────────────
// Asking the server rather than guessing: only it knows where the keyframes
// are, and the snapped start is the number the user actually needs to see.
let planTimer = null
watch([segments, precise], () => {
  plan.value = null
  planErr.value = ''
  clearTimeout(planTimer)
  if (!segments.value.length) return
  planTimer = setTimeout(fetchPlan, 200)
}, { deep: true })

function body() {
  return JSON.stringify({
    path: props.video.path,
    segments: segments.value.map(s => [s.start, s.end]),
    precise: precise.value,
    gpu: gpu.value,
  })
}

async function fetchPlan() {
  try {
    const d = await api('/api/clip/plan', { method: 'POST', body: body() })
    if (!d.ok) { planErr.value = d.error || '無效的片段'; return }
    plan.value = d
  } catch (e) {
    planErr.value = e.detail || String(e.status)
  }
}

// ── running ────────────────────────────────────────────────────────────────
let pollTimer = null

async function start() {
  if (!segments.value.length || running.value) return
  running.value = true
  pct.value = 0
  planErr.value = ''
  try {
    const { job_id } = await api('/api/clip/run',
                                 { method: 'POST', body: body() })
    await poll(job_id)
  } catch (e) {
    planErr.value = e.detail || String(e.status)
    running.value = false
  }
}

function poll(jobId) {
  return new Promise(resolve => {
    const tick = async () => {
      try {
        const j = await api(`/api/clip/job/${jobId}`)
        if (j.status === 'running') {
          pct.value = j.progress?.pct || 0
          pollTimer = setTimeout(tick, 400)
          return
        }
        running.value = false
        if (j.status === 'error') planErr.value = j.error || '剪輯失敗'
        else {
          made.value.unshift(j.result)
          emit('created', j.result)
          setStatus(`已剪出 ${j.result.name}`, 'ok')
          // ready for the next one; the player keeps its position
          segments.value = []
          inT.value = null
          outT.value = null
        }
      } catch (e) {
        running.value = false
        planErr.value = e.detail || String(e.status)
      }
      resolve()
    }
    tick()
  })
}

function onKey(e) {
  const t = e.target
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
            t.isContentEditable)) return
  if (e.ctrlKey || e.altKey || e.metaKey) return
  if (e.key === 'i' || e.key === 'I') { markIn(); e.preventDefault() }
  else if (e.key === 'o' || e.key === 'O') { markOut(); e.preventDefault() }
  else if (e.key === 'Enter' && canAdd.value) { addSegment(); e.preventDefault() }
}

onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  clearTimeout(planTimer)
  clearTimeout(pollTimer)
})
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="box">
      <div class="head">
        <span class="ib"><Icon name="scissors" :size="14" /> 剪輯</span>
        <span class="nm" :title="video.name">{{ video.name }}</span>
        <span class="spacer"></span>
        <button class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 關閉
        </button>
      </div>

      <VideoPlayer ref="player" :src="streamUrl(video.path)"
                   @close="emit('close')" />

      <div class="marks">
        <button class="mbtn ib" @click="markIn">
          <Icon name="markIn" :size="13" /> 起點 <kbd>I</kbd>
        </button>
        <span class="tval" :class="{ set: inT !== null }"
              :title="inT !== null ? '點一下跳到起點' : ''"
              @click="seekTo(inT)">{{ hms(inT) }}</span>
        <span class="nudge" v-if="inT !== null">
          <button @click="nudge('in', -1)">−1s</button>
          <button @click="nudge('in', -0.1)">−.1</button>
          <button @click="nudge('in', 0.1)">+.1</button>
          <button @click="nudge('in', 1)">+1s</button>
        </span>

        <span class="arrow">→</span>

        <button class="mbtn ib" @click="markOut">
          <Icon name="markOut" :size="13" /> 終點 <kbd>O</kbd>
        </button>
        <span class="tval" :class="{ set: outT !== null }"
              :title="outT !== null ? '點一下跳到終點' : ''"
              @click="seekTo(outT)">{{ hms(outT) }}</span>
        <span class="nudge" v-if="outT !== null">
          <button @click="nudge('out', -1)">−1s</button>
          <button @click="nudge('out', -0.1)">−.1</button>
          <button @click="nudge('out', 0.1)">+.1</button>
          <button @click="nudge('out', 1)">+1s</button>
        </span>

        <span v-if="span !== null" class="len">{{ hms(span) }}</span>
        <button v-if="inT !== null" class="lnk" @click="preview">預覽起點</button>
        <span class="spacer"></span>
        <button class="btn small ib" :disabled="!canAdd" @click="addSegment">
          <Icon name="plus" :size="13" />
          {{ editing ? '更新片段' : '加入片段' }} <kbd>Enter</kbd>
        </button>
        <button v-if="editing" class="lnk" @click="cancelEdit">取消調整</button>
      </div>

      <div v-if="segments.length" class="segs">
        <div v-for="(s, i) in segments" :key="s.id" class="seg"
             :class="{ editing: s.id === editingId }">
          <span class="sn">{{ i + 1 }}</span>
          <span class="stime">{{ hms(s.start) }} – {{ hms(s.end) }}</span>
          <span class="dim">{{ hms(s.end - s.start, 0) }}</span>
          <button class="lnk" @click="playSegment(s)">播放</button>
          <button class="lnk" v-if="s.id !== editingId"
                  @click="editSegment(s)">調整</button>
          <span v-else class="badge">調整中 — 改好按「更新片段」</span>
          <span class="spacer"></span>
          <button class="xbtn" title="移除這段" @click="dropSegment(s.id)">
            <Icon name="close" :size="12" />
          </button>
        </div>
        <div class="segfoot">
          共 <b>{{ segments.length }}</b> 段 · 合計 {{ hms(total, 0) }}
          <span class="dim">· 依時間先後接成一個檔案</span>
        </div>
      </div>
      <div v-else class="segs empty">
        邊看邊按 <kbd>I</kbd> 標起點、<kbd>O</kbd> 標終點，再「加入片段」（至少 1 秒）。
        加入多段會<b>接成一個檔案</b>。
      </div>

      <div class="opts">
        <label class="ib"><input type="checkbox" v-model="precise" /> 精準切割（重編碼）</label>
        <label v-if="precise" class="ib"><input type="checkbox" v-model="gpu" /> 用 GPU</label>
        <span class="hint">
          <template v-if="!precise">
            複製切割：無損、實測 3 段 0.8 秒，但每段起點只能落在關鍵幀
          </template>
          <template v-else>
            每段都準到格，但要重新編碼 —— 實測 3 段共 2.9 分鐘，GPU 17 秒、CPU 約 1.5 分
          </template>
        </span>
      </div>

      <div v-if="planErr" class="err">{{ planErr }}</div>

      <div v-else-if="plan" class="plan">
        <div>
          輸出 <b>{{ plan.out_name }}</b>
          <span class="dim">· {{ hms(plan.duration, 0) }}</span>
          <span class="dim">· 約 {{ fmtSize(plan.est_size) }}</span>
          <span class="dim">· 與原檔同一個資料夾</span>
        </div>
        <div v-if="plan.merged" class="warn ib">
          <Icon name="warn" :size="12" /> 有重疊的片段已合併，實際會輸出
          {{ plan.parts.length }} 段
        </div>
        <div v-for="(q, i) in plan.parts" :key="i" class="pline">
          <template v-if="q.snapped && q.drift > 0.01">
            <span class="dim">第 {{ i + 1 }} 段</span>
            實際從 <b>{{ hms(q.actual_start) }}</b> 開始（早了
            {{ q.drift.toFixed(2) }} 秒）
          </template>
          <template v-else-if="!q.snapped && !precise">
            <span class="dim">第 {{ i + 1 }} 段</span>
            找不到關鍵幀位置，實際起點可能早幾秒
          </template>
        </div>
        <div v-if="!precise && plan.parts.some(q => q.drift > 0.01)"
             class="warn ib">
          <Icon name="warn" :size="12" /> 複製切割只能從關鍵幀開始。要準到格請勾「精準切割」
        </div>
        <div v-if="!plan.enough_space" class="err">
          空間不足：剩餘 {{ fmtSize(plan.free) }}
        </div>
      </div>

      <div class="foot">
        <button class="btn" :disabled="!segments.length || running ||
                                       (plan && !plan.enough_space)"
                @click="start">
          <Icon v-if="running" name="spinner" :size="14" class="spin" />
          <Icon v-else name="scissors" :size="14" />
          {{ running
              ? `剪輯中… ${Math.round(pct * 100)}%`
              : segments.length > 1
                ? `剪出 1 個檔案（${segments.length} 段接起來）`
                : '開始剪輯' }}
        </button>
        <span class="spacer"></span>
        <span class="dim">原始檔案不會被更動；要刪除請用「刪除模式」</span>
      </div>

      <div v-if="made.length" class="made">
        <div class="mhdr">這次剪出的檔案</div>
        <div v-for="m in made" :key="m.path" class="mrow">
          <Icon name="check" :size="13" class="ok" />
          <span class="mname" :title="m.path">{{ m.name }}</span>
          <span class="dim">
            {{ hms(m.duration, 0) }}
            <template v-if="m.segments > 1">· {{ m.segments }} 段</template>
            · {{ fmtSize(m.size) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.box { background: var(--panel); border-radius: 10px; padding: 12px;
  width: min(1100px, 94vw); max-height: 94vh; max-height: 94dvh; overflow: auto;
  display: flex; flex-direction: column; gap: 8px; }
.head { display: flex; align-items: center; gap: 10px; color: var(--text);
  font-size: 13px; }
.nm { color: var(--dim); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.spacer { flex: 1; }
.marks { display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 12px; color: var(--text); }
.mbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 4px 10px; cursor: pointer; }
.mbtn:hover { border-color: var(--accent); }
.btn.small { font-size: 11px; padding: 4px 10px; }
.tval { font-family: monospace; font-size: 13px; color: var(--dim);
  min-width: 74px; text-align: center; }
.tval.set { color: var(--accent); cursor: pointer; }
.arrow { color: var(--dim); margin: 0 4px; }
.nudge { display: inline-flex; gap: 2px; }
.nudge button { background: none; border: 1px solid #444; border-radius: 3px;
  color: var(--dim); font-size: 10px; padding: 2px 5px; cursor: pointer; }
.nudge button:hover { border-color: var(--accent); color: var(--accent); }
.len { font-size: 12px; color: var(--accent); font-family: monospace; }
/* The player used to be free to take 74vh while this list took another 26vh,
   which does not fit in the 94vh box — anything below the player fell off the
   bottom and had to be scrolled to. Nobody noticed while segments were marked
   one at a time against the player; arriving from the contact sheet with
   several already in the list made it happen every time. So inside this
   dialog the picture gives way to the list it exists to produce. */
.box :deep(video) { max-height: 50vh; max-height: 50dvh; }
.segs { background: var(--card); border-radius: 6px; padding: 6px 10px;
  font-size: 12px; color: var(--text); max-height: 26vh; max-height: 26dvh;
  min-height: 58px; overflow: auto; }
.seg.editing { background: rgba(78, 161, 255, .12); border-radius: 4px;
  padding-left: 4px; padding-right: 4px; }
.badge { font-size: 11px; color: var(--accent); }
.segs.empty { color: var(--dim); line-height: 1.7; }
.seg { display: flex; align-items: center; gap: 8px; padding: 3px 0;
  border-bottom: 1px solid #2a2a3c; }
.seg:last-of-type { border-bottom: none; }
.sn { color: var(--dim); min-width: 16px; }
.stime { font-family: monospace; color: var(--accent); }
.xbtn { background: none; border: none; color: var(--dim); cursor: pointer;
  display: inline-flex; }
.xbtn:hover { color: var(--danger); }
.segfoot { padding-top: 5px; border-top: 1px solid #2a2a3c; margin-top: 4px;
  color: var(--dim); }
.segfoot b { color: var(--accent); }
.opts { display: flex; align-items: center; gap: 14px; font-size: 12px;
  color: var(--text); flex-wrap: wrap; }
.hint { color: var(--dim); font-size: 11px; }
.plan { font-size: 12px; color: var(--text); line-height: 1.7;
  background: var(--card); border-radius: 6px; padding: 8px 10px; }
.pline:empty { display: none; }
.dim { color: var(--dim); }
.warn { color: #e0b070; font-size: 11px; }
.err { color: var(--danger); font-size: 12px; }
.foot { display: flex; align-items: center; gap: 10px; font-size: 11px; }
.made { border-top: 1px solid #313148; padding-top: 8px; }
.mhdr { color: var(--dim); font-size: 11px; margin-bottom: 4px; }
.mrow { display: flex; align-items: center; gap: 8px; font-size: 12px;
  padding: 2px 0; }
.mname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ok { color: var(--success); }
kbd { background: #2a2a3c; border: 1px solid #555; border-radius: 3px;
  padding: 0 4px; font-size: 10px; font-family: monospace; }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
