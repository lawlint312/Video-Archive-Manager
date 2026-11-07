<script setup>
/**
 * Re-encode files that are paying for quality nobody asked for.
 *
 * Lossy and irreversible, so the whole design leans on two things: it always
 * writes a *new* file (the original is yours to delete, in delete mode, like
 * everything else), and you can watch a 10-second before/after of any file
 * before committing to a batch.
 */
import { ref, computed, watch, nextTick } from 'vue'
import { api, errText } from '../api'
import { setStatus, setBusy } from '../store'
import { fmtSize, fmtDur } from '../dedup'
import { estimateSaving, calibrate } from '../shrinkestimate.js'
import ScopePicker from './ScopePicker.vue'
import VideoCompare from './VideoCompare.vue'
import Icon from './Icon.vue'
import { t, N_ } from '../i18n'
import { ts } from '../serverstrings.js'

const selected = ref([])
const scopeOpen = ref(true)
const scanning = ref(false)
const running = ref(false)
const progress = ref('')
const pct = ref(0)
const error = ref('')
const items = ref(null)
const result = ref(null)
const chosen = ref(new Set())

const minMbps = ref(18)
const crf = ref(26)
const maxEdge = ref(0)
// H.265 by default: smaller files, and it runs on the GPU, so the CPU stays
// free for everyone else — see CODEC_TABLE and core/shrink.py
const codec = ref('h265')
const showCodecHelp = ref(false)
const showCrfHelp = ref(false)
const showEdgeHelp = ref(false)
const showMbpsHelp = ref(false)

// measured; see core/shrink.py for how these were produced
// [percent saved, SSIM] — the words are added where it is shown
const CRF_TABLE = [
  { crf: 20, ord: [41, '0.9939'], big: [64, '0.9935'] },
  { crf: 23, ord: [58, '0.9923'], big: [77, '0.9918'] },
  { crf: 26, ord: [70, '0.9902'], big: [86, '0.9898'] },
  { crf: 28, ord: [76, '0.9884'], big: [89, '0.9882'] },
]
const EDGE_TABLE = [
  { name: N_('原解析度 crf 26'), sec: 52, save: 86 },
  { name: N_('縮 1440 crf 23'), sec: 30, save: 91 },
  { name: N_('縮 1080 crf 26'), sec: 18, save: 96, approx: true },
]
// measured on a 60 s slice of a real 10.3 Mbps 1080p film, every row at the
// quality of crf 26 (SSIM 0.991); `others` is how much slower the same piece
// of server work ran while the encode was going
const CODEC_TABLE = [
  { v: 'h264', name: N_('H.264（CPU）'), save: 76.5, sec: 24, others: 34 },
  { v: 'h265', name: N_('H.265（GPU）'), save: 84, sec: 11, others: 1 },
  { v: '', name: N_('H.265，沒有 GPU 時（CPU）'), save: 86, sec: 39, others: 61 },
]
const EDGES = [
  { v: 0, label: N_('原樣（不縮）') },
  { v: 2560, label: N_('長邊 2560（1440）') },
  { v: 1920, label: N_('長邊 1920（1080）') },
  { v: 1280, label: N_('長邊 1280（720）') },
]

// The estimate follows the settings above (crf, codec, max edge) and is
// corrected by what past shrinks really saved — see shrinkestimate.js.
const results = ref([])
async function loadResults() {
  try { results.value = (await api('/api/shrink/results')).results } catch { /* ignore */ }
}
loadResults()
const cal = computed(() => calibrate(results.value))
// every result counts: a codec with none of its own borrows the overall figure
const calN = computed(() => cal.value.n.all)
const settings = computed(() => ({ crf: Number(crf.value), codec: codec.value,
                                   maxEdge: Number(maxEdge.value) }))
const est = i => estimateSaving(i, settings.value, cal.value)

const picked = computed(() =>
  (items.value || []).filter(i => chosen.value.has(i.path)))
const pickedSaving = computed(() =>
  picked.value.reduce((n, i) => n + est(i), 0))

function toggle(i) {
  const s = new Set(chosen.value)
  s.has(i.path) ? s.delete(i.path) : s.add(i.path)
  chosen.value = s
}
function setAll(on) {
  chosen.value = on ? new Set((items.value || []).map(i => i.path)) : new Set()
}

async function poll(jobId, url, onProgress) {
  for (;;) {
    await new Promise(r => setTimeout(r, 500))
    const j = await api(`${url}/${jobId}`)
    if (j.status === 'running') { onProgress?.(j.progress); continue }
    if (j.status === 'error') throw { detail: j.error }
    return j.result
  }
}

async function scan() {
  scanning.value = true
  error.value = ''
  result.value = null
  sample.value = null
  try {
    const { job_id } = await api('/api/shrink/scan', {
      method: 'POST',
      body: JSON.stringify({ roots: selected.value,
                             min_mbps: Number(minMbps.value) || 0 }),
    })
    const res = await poll(job_id, '/api/shrink/job', p => {
      progress.value = ts(p) || ''
      setBusy(p ? t('壓縮掃描：') + ts(p) : '')
    })
    items.value = res
    loadResults()           // the scan may have seeded it from older shrinks
    // nothing pre-selected: this is lossy, every file is a deliberate choice
    chosen.value = new Set()
    scopeOpen.value = false
  } catch (e) {
    error.value = t('掃描失敗：') + errText(e)
  } finally { scanning.value = false; setBusy('') }
}

// ── preview ────────────────────────────────────────────────────────────────
const sample = ref(null)
const sampling = ref(false)

async function preview(i) {
  sampling.value = true
  error.value = ''
  try {
    const d = await api('/api/shrink/sample', {
      method: 'POST',
      body: JSON.stringify({ path: i.path, crf: Number(crf.value),
                             max_edge: Number(maxEdge.value), codec: codec.value }),
    })
    sample.value = { ...d, item: i }
  } catch (e) {
    error.value = t('試看失敗：') + errText(e)
  } finally { sampling.value = false }
}
const playUrl = p => `/api/shrink/sample/play?path=${encodeURIComponent(p)}`

// ── pending pairs ──────────────────────────────────────────────────────────
// The decision "keep which one?" happens long after the run — a batch takes
// far too long to sit and watch — so it lives in a list that survives closing
// the dialog, not in the run's result panel.
const pending = ref([])
const renameAfter = ref(false)
const resolving = ref('')
const compare = ref(null)      // the pair currently open for side-by-side
const pendEl = ref(null)       // scroll target once a run finishes
const doneOpen = ref(false)    // the completion list, collapsed by default

async function loadPending() {
  try { pending.value = (await api('/api/pairs')).pairs } catch { /* ignore */ }
}
loadPending()

const pendingSaving = computed(() =>
  pending.value.reduce((n, p) => n + Math.max(0, p.saved), 0))

const KIND = { shrink: N_('壓縮'), repair: N_('修復'), conceal: N_('遮蔽損壞') }

// For a concealed re-encode: take both players to just before a damaged
// stretch. Two whole films side by side give no clue where a one-second
// glitch is, so the scan's locations are the only way to compare usefully.
const cmp = ref(null)
const LEAD_IN = 3
async function jumpTo(p, g) {
  if (compare.value?.produced !== p.produced) compare.value = p
  await nextTick()
  cmp.value?.seekBoth(g.start - LEAD_IN)
}
function span(g) {
  return g.end - g.start >= 1 ? `${mmss(g.start)}～${mmss(g.end)}` : mmss(g.start)
}
function mmss(t) {
  t = Math.max(0, Math.floor(t))
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`
}

async function resolvePair(p, action) {
  const what = action === 'keep_produced' ? p.original_name
             : action === 'keep_original' ? p.produced_name : null
  if (what && !confirm(t('永久刪除 {name}？\n\n此操作無法復原。', { name: what }) +
      (action === 'keep_produced' && renameAfter.value
        ? '\n\n' + t('保留的檔案會改名為 {name}。', { name: p.original_name }) : ''))) return
  resolving.value = p.produced
  try {
    const r = await api('/api/pairs/resolve', {
      method: 'POST',
      body: JSON.stringify({ original: p.original, produced: p.produced,
                             action, rename: renameAfter.value }),
    })
    if (compare.value?.produced === p.produced) compare.value = null
    await loadPending()
    setStatus(ts(r.note) || (action === 'keep_both' ? t('兩個都保留') : t('已處理')),
              r.note ? 'warn' : 'ok')
  } catch (e) {
    error.value = t('處理失敗：') + errText(e)
  } finally { resolving.value = '' }
}

function openCompare(p) {
  compare.value = compare.value?.produced === p.produced ? null : p
}
const streamUrlFor = p => `/api/stream?path=${encodeURIComponent(p)}`

async function run() {
  if (!picked.value.length) return
  const n = picked.value.length
  if (!confirm(t('壓縮 {n} 個檔案？\n\n會另存為新檔（原始檔案不會被刪除或修改）。\n重新編碼是失真的，無法還原成原本的畫質。\n\n粗估可省 {size}。',
                 { n, size: fmtSize(pickedSaving.value) }))) return
  running.value = true
  error.value = ''
  result.value = null
  pct.value = 0
  try {
    const { job_id } = await api('/api/shrink/run', {
      method: 'POST',
      body: JSON.stringify({ paths: picked.value.map(i => i.path),
                             crf: Number(crf.value),
                             max_edge: Number(maxEdge.value),
                             codec: codec.value }),
    })
    const res = await poll(job_id, '/api/shrink/job', p => {
      if (p && typeof p === 'object') {
        pct.value = Math.round((p.pct || 0) * 100)
        progress.value = `(${p.index}/${p.total}) ${p.name} — ${pct.value}%`
        setBusy(t('壓縮中 {progress}', { progress: progress.value }))
      }
    })
    result.value = res
    setStatus(t('壓縮完成：{n} 個', { n: res.done.length }) +
              (res.saved > 0 ? t('，省下 {size}', { size: fmtSize(res.saved) }) : '') +
              (res.failed.length ? t('，{n} 個失敗', { n: res.failed.length }) : ''),
              res.failed.length ? 'warn' : 'ok')
    const done = new Set(res.done.map(d => d.src))
    items.value = items.value.filter(i => !done.has(i.path))
    chosen.value = new Set([...chosen.value].filter(p => !done.has(p)))
    doneOpen.value = false       // a fresh run collapses back to the summary
    await loadPending()          // the new pairs are now waiting on a decision
    loadResults()                // and the estimate learns from them
    // Land on the decisions, not on a list of things already known to have
    // worked — checking the pairs is the whole reason the run finished.
    await nextTick()
    pendEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (e) {
    error.value = t('壓縮失敗：') + errText(e)
  } finally { running.value = false; setBusy('') }
}
</script>

<template>
  <div class="wrap">
    <button v-if="!scopeOpen" class="scopebar" @click="scopeOpen = true">
      <Icon name="chevronRight" :size="13" />
      {{ t('範圍：已選 {n} 個資料夾', { n: selected.length }) }}
      <span class="chg">{{ t('重新選擇') }}</span>
    </button>

    <div v-show="scopeOpen" class="scopewrap">
      <ScopePicker storage-key="vam.shrink.picked" :default-all="false"
                   :remember="false" :disabled="scanning || running"
                   @update:selected="v => selected = v" />
      <div class="opts">
        <label class="lab">{{ t('位元率門檻') }}</label>
        <input type="number" v-model.number="minMbps" min="0" max="200"
               step="1" style="width:78px" /> <span class="unit">{{ t('Mbps 以上才列出') }}</span>
        <button class="qmark" :class="{ on: showMbpsHelp }" :title="t('門檻會影響什麼')"
                @click="showMbpsHelp = !showMbpsHelp">?</button>
        <span class="spacer"></span>
        <button class="btn scanbtn" :disabled="scanning || running || !selected.length"
                @click="scan">{{ scanning ? t('掃描中…') : t('掃描') }}</button>
      </div>
      <div v-if="showMbpsHelp" class="tip">
        {{ t('門檻') }}<b>{{ t('不會讓掃描變快') }}</b>{{ t(' —— 要知道位元率就得逐檔探測，所以每個檔案都會被檢查。它只決定清單有多長。這個庫實測：18 Mbps 約 125 列、6 Mbps 約 977 列、不設限 5,580 列。清單依「粗估可省」排序。') }}
      </div>
    </div>

    <div class="opts params">
      <label class="lab">
        {{ t('編碼') }}
        <button class="qmark" :class="{ on: showCodecHelp }" @click="showCodecHelp = !showCodecHelp">?</button>
      </label>
      <label class="radio"><input type="radio" value="h264" v-model="codec" :disabled="running" /> H.264</label>
      <label class="radio"><input type="radio" value="h265" v-model="codec" :disabled="running" /> H.265</label>
      <label class="lab">
        {{ t('品質 crf') }}
        <button class="qmark" @click="showCrfHelp = !showCrfHelp">?</button>
      </label>
      <select v-model.number="crf" :disabled="running" style="width:70px">
        <option v-for="c in [20,23,26,28]" :key="c" :value="c">{{ c }}</option>
      </select>
      <span class="unit">{{ t('數字越大，檔案越小、畫質越低') }}</span>
      <label class="lab">
        {{ t('解析度上限') }}
        <button class="qmark" @click="showEdgeHelp = !showEdgeHelp">?</button>
      </label>
      <select v-model.number="maxEdge" :disabled="running" style="width:180px">
        <option v-for="e in EDGES" :key="e.v" :value="e.v">{{ t(e.label) }}</option>
      </select>
    </div>

    <div v-if="showCodecHelp" class="helptbl">
      <div class="hh">{{ t('實測（1080p 影片的 60 秒切片，畫質都相當於 crf 26）') }}</div>
      <table>
        <thead>
          <tr><th>{{ t('編碼') }}</th><th>{{ t('省下') }}</th><th>{{ t('耗時') }}</th><th>{{ t('同時其他人變慢') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in CODEC_TABLE" :key="r.name" :class="{ cur: r.v === codec }">
            <td>{{ t(r.name) }}</td><td>{{ r.save }}%</td>
            <td>{{ t('{n} 秒', { n: r.sec }) }}</td><td>{{ r.others }}%</td>
          </tr>
        </tbody>
      </table>
      <div class="hn">
        {{ t('H.265 的檔案小約三成，而且交給顯示卡編碼，壓縮時幾乎不佔 CPU。代價是') }}<b>{{ t('較舊的裝置不能播') }}</b>{{ t('：要 Safari／iPhone，或 Chrome／Edge 搭配有 H.265 硬體解碼的顯示卡或手機（約 2016 年以後）。沒有 NVIDIA 顯示卡時自動改用 CPU，檔案一樣小，但比較慢、也比較佔 CPU。') }}
      </div>
    </div>

    <div v-if="showCrfHelp" class="helptbl">
      <div class="hh">{{ t('實測：同一個 crf 在不同來源上畫質幾乎相同，變的只有省多少') }}</div>
      <table>
        <thead>
          <tr><th>crf</th><th>{{ t('一般 1080p（6.1 Mbps）') }}</th><th>{{ t('過度編碼的 4K（52 Mbps）') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in CRF_TABLE" :key="r.crf" :class="{ cur: r.crf === crf }">
            <td>{{ r.crf }}</td>
            <td>{{ t('省 {pct}%，SSIM {ssim}', { pct: r.ord[0], ssim: r.ord[1] }) }}</td>
            <td>{{ t('省 {pct}%，SSIM {ssim}', { pct: r.big[0], ssim: r.big[1] }) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="hn">
        {{ t('SSIM 1.000 表示與原片無異，0.98 以上一般肉眼看不出差別。因為 crf 是') }}<b>{{ t('品質目標') }}</b>{{ t('而不是壓縮率目標，所以一個值可以走遍全庫。') }}
        {{ t('表裡是 H.264；選 H.265 時同一個數字對應到相同的畫質，只是檔案更小。') }}
      </div>
    </div>

    <div v-if="showEdgeHelp" class="helptbl">
      <div class="hh">{{ t('實測（4K60 影片的 30 秒切片）') }}</div>
      <table>
        <thead>
          <tr><th>{{ t('設定') }}</th><th>{{ t('耗時') }}</th><th>{{ t('省下') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in EDGE_TABLE" :key="r.name"><td>{{ t(r.name) }}</td>
            <td>{{ r.approx ? t('約 {n} 秒', { n: r.sec }) : t('{n} 秒', { n: r.sec }) }}</td>
            <td>{{ r.approx ? t('約 {n}%', { n: r.save }) : r.save + '%' }}</td></tr>
        </tbody>
      </table>
      <div class="hn">
        {{ t('表裡是 H.264。降幀率也試過：60→30 fps 省不到 1%，所以沒有這個選項。') }}
      </div>
    </div>

    <div v-if="scanning || running" class="prog">
      <Icon name="spinner" :size="13" class="spin" /> {{ progress }}
      <span class="bg">{{ running ? t('壓縮進行中，無法關閉此視窗')
                                  : t('可關閉此視窗，但掃描結果不會保留') }}</span>
    </div>
    <div v-if="running" class="bar"><div class="fill" :style="{ width: pct + '%' }"></div></div>
    <div v-if="error" class="err">{{ error }}</div>

    <!-- before / after -->
    <div v-if="sample" class="sample">
      <div class="shead">
        <b>{{ t('試看') }}</b> · {{ sample.item.name }} · {{ t('從 {at} 起 {n} 秒', { at: fmtDur(sample.at), n: sample.seconds }) }}
        <span class="spacer"></span>
        <button class="lnk" @click="sample = null">{{ t('收起') }}</button>
      </div>

      <VideoCompare :left-src="playUrl(sample.before)"
                    :right-src="playUrl(sample.after)"
                    :left-label="t('原始')" :right-label="t('壓縮後')" preload="auto">
        <template #left-meta> · {{ fmtSize(sample.before_size) }} ·
          {{ sample.width }}×{{ sample.height }}</template>
        <template #right-meta> · {{ fmtSize(sample.after_size) }} ·
          {{ sample.out_width }}×{{ sample.out_height }}</template>
      </VideoCompare>

      <div class="verdict">
        {{ t('這一段實際省下') }} <b>{{ Math.round(sample.saved * 100) }}%</b>
        {{ t('—— 照這個比例，整支片會從 {size} 變成約', { size: fmtSize(sample.item.size) }) }}
        <b>{{ fmtSize(sample.projected_size) }}</b>{{ t('。') }}
        <span class="dim">{{ t('（這是真的編出來的數字；清單上的「粗估」只夠用來排序）') }}</span>
      </div>
    </div>

    <!-- result -->
    <!-- Collapsed by default, because every line here says the same thing as
         a row of 待決定 below (same two files, same before/after sizes) — and
         that is the list you actually work from. Failures are the exception:
         a file that failed produced nothing, so it has no pending pair and
         this is the only place it appears. Those always show. -->
    <div v-if="result" class="done">
      <button class="doneline ib" @click="doneOpen = !doneOpen">
        <Icon name="check" :size="15" />
        {{ t('完成 {n} 個', { n: result.done.length }) }}<template v-if="result.saved > 0">
          · {{ t('省下 {size}', { size: fmtSize(result.saved) }) }}</template>
        <span v-if="result.done.length" class="lnk">
          {{ doneOpen ? t('收起明細') : t('展開明細') }}</span>
      </button>
      <template v-if="doneOpen">
        <div v-for="d in result.done" :key="d.dst" class="okrow">
          {{ d.name }} — {{ fmtSize(d.before) }} → {{ fmtSize(d.after) }}
          {{ t('（省 {n}%）；原始檔案已保留', { n: Math.round(d.ratio * 100) }) }}
        </div>
      </template>
      <div v-for="f in result.failed" :key="f.path" class="failrow">
        {{ f.path.split(/[\\/]/).pop() }} — {{ ts(f.error) }}
      </div>
    </div>

    <div v-if="items && !scanning && !scopeOpen" class="results">
      <div v-if="!items.length" class="empty ib">
        <Icon name="check" :size="15" /> {{ t('這個範圍內沒有位元率達到 {n} Mbps 的檔案。把門檻調低可以看到更多。', { n: minMbps }) }}
      </div>
      <template v-else>
        <div class="shead">
          {{ t('共 {n} 個候選', { n: items.length }) }} · {{ t('已選') }} <b>{{ picked.length }}</b>
          <template v-if="picked.length"> · {{ t('粗估可省 {size}', { size: fmtSize(pickedSaving) }) }}</template>
          <span class="dim calib">{{ calN
            ? t('（依目前設定，並以你實際壓過的 {n} 支校正）', { n: calN })
            : t('（依目前設定；還沒有實際壓過的結果可以校正）') }}</span>
          <span class="spacer"></span>
          <button class="lnk" @click="setAll(true)">{{ t('全選') }}</button>
          <button class="lnk" @click="setAll(false)">{{ t('全不選') }}</button>
        </div>
        <div class="hdr">
          <span class="cb"></span><span class="c1">Mbps</span>
          <span class="c2">{{ t('解析度') }}</span><span class="c3">{{ t('時長') }}</span>
          <span class="c4">{{ t('大小') }}</span><span class="c5">{{ t('粗估可省') }}</span>
          <span class="fn">{{ t('檔名') }}</span><span class="c6"></span>
        </div>
        <label v-for="i in items" :key="i.path" class="row">
          <input type="checkbox" class="cb" :checked="chosen.has(i.path)"
                 :disabled="running" @change="toggle(i)" />
          <span class="c1 num">{{ i.mbps }}</span>
          <span class="c2">{{ i.width }}×{{ i.height }}</span>
          <span class="c3">{{ fmtDur(i.duration) }}</span>
          <span class="c4">{{ fmtSize(i.size) }}</span>
          <span class="c5 save">{{ fmtSize(est(i)) }}</span>
          <span class="fn" :title="i.path">{{ i.name }}</span>
          <button class="c6 lnk" :disabled="sampling || running"
                  @click.prevent="preview(i)">
            {{ sampling ? '…' : t('試看') }}</button>
        </label>
      </template>
    </div>

    <div v-if="items && items.length && !scopeOpen" class="foot">
      <button class="btn" :disabled="!picked.length || running" @click="run">
        <Icon name="refresh" :size="14" />
        {{ running ? t('壓縮中…') : t('壓縮這 {n} 個', { n: picked.length }) }}
      </button>
      <span class="dim">{{ t('另存為新檔，原始檔案不會被刪除或修改') }}</span>
    </div>

    <!-- Waiting on a decision. Survives closing the dialog on purpose: a batch
         of 125 files takes far too long to sit and watch, so the choice is
         always made later. A pair disappears from here the moment one side is
         gone, so "done" needs no state of its own. -->
    <div v-if="pending.length" ref="pendEl" class="pend">
      <div class="phead">
        <Icon name="wrench" :size="13" />
        <b>{{ t('待決定：{n} 組', { n: pending.length }) }}</b>
        <span class="dim">{{ t('刪掉原檔可再收回 {size}', { size: fmtSize(pendingSaving) }) }}</span>
        <span class="spacer"></span>
        <label class="ck ib"><input type="checkbox" v-model="renameAfter" />
          {{ t('保留新檔時改回原本的檔名') }}</label>
      </div>
      <div class="pnote">
        {{ t('這些是「壓縮」「修復」和「遮蔽損壞」產生的新檔，兩個版本現在都在。') }}
        <b>{{ t('看過再決定') }}</b>{{ t(' —— 刪除是永久的。') }}
      </div>

      <div v-for="p in pending" :key="p.produced" class="prow">
        <div class="pline">
          <span class="tagk" :class="p.kind">{{ KIND[p.kind] ? t(KIND[p.kind]) : p.kind }}</span>
          <span class="pn" :title="p.original">{{ p.original_name }}</span>
          <span class="arrow">→</span>
          <span class="pn new" :title="p.produced">{{ p.produced_name }}</span>
          <span class="spacer"></span>
          <span class="dim">{{ fmtSize(p.original_size) }} →
            {{ fmtSize(p.produced_size) }}</span>
          <span :class="p.saved > 0 ? 'save' : 'grow'">
            {{ p.saved > 0 ? t('省 {size}', { size: fmtSize(p.saved) }) : t('多 {size}', { size: fmtSize(-p.saved) }) }}</span>
        </div>
        <div class="pline sub">
          <span v-if="p.kind === 'shrink'" class="dim"><template
            v-if="p.codec === 'h265'">H.265 · </template>crf {{ p.crf }}<template
            v-if="p.max_edge"> · {{ t('長邊 {n}', { n: p.max_edge }) }}</template></span>
          <span v-else-if="p.kind === 'conceal'" class="dim ib regs">
            {{ t('損壞處：') }}<template v-if="(p.regions || []).length">
              <button v-for="(g, gi) in p.regions.slice(0, 8)" :key="gi" class="chipbtn"
                      :title="t('兩邊一起跳到 {time} 前 {n} 秒', { time: mmss(g.start), n: LEAD_IN })"
                      @click="jumpTo(p, g)">{{ span(g) }}</button>
              <template v-if="p.regions.length > 8">{{ t('…還有 {n} 處', { n: p.regions.length - 8 }) }}</template>
            </template><template v-else>{{ t('找不到位置') }}</template>
          </span>
          <span v-else class="dim">{{ t('丟掉 {n} 段 · 約損失 {sec} 秒', { n: p.dropped, sec: p.lost_seconds }) }}</span>
          <span class="spacer"></span>
          <button class="mini" :disabled="resolving === p.produced"
                  @click="openCompare(p)">
            {{ compare && compare.produced === p.produced ? t('收起對照') : t('左右對照') }}</button>
          <button class="mini" :disabled="resolving === p.produced"
                  @click="resolvePair(p, 'keep_produced')">{{ t('留新檔') }}</button>
          <button class="mini" :disabled="resolving === p.produced"
                  @click="resolvePair(p, 'keep_original')">{{ t('留原檔') }}</button>
          <button class="mini" :disabled="resolving === p.produced"
                  @click="resolvePair(p, 'keep_both')">{{ t('兩個都留') }}</button>
        </div>

        <div v-if="compare && compare.produced === p.produced" class="pcmp">
          <VideoCompare :ref="el => cmp = el" :left-src="streamUrlFor(p.original)"
                        :right-src="streamUrlFor(p.produced)"
                        :left-label="t('原檔')" :right-label="t('新檔')">
            <template #left-meta> · {{ fmtSize(p.original_size) }}</template>
            <template #right-meta> · {{ fmtSize(p.produced_size) }}</template>
          </VideoCompare>
          <div v-if="p.kind === 'conceal'" class="cmpnote">
            {{ t('點上面的') }}<b>{{ t('損壞處') }}</b>{{ t('時間，兩邊會一起跳到那裡的前 {n} 秒開始播。', { n: LEAD_IN }) }}
            {{ t('左邊原檔播到損壞處') }}<b>{{ t('可能直接停住') }}</b>{{ t(' —— 那正是要修的問題；右邊應該能一路播過去，損壞那一兩秒會看到花屏或停頓。') }}
          </div>
          <div v-else class="cmpnote">
            {{ t('這裡比的是') }}<b>{{ t('完整的檔案') }}</b>{{ t('，不是試看用的 10 秒樣本 —— 決定要不要刪掉原檔，應該看真東西。') }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The panel is the scrolling area. It used to have neither `flex` nor
   `overflow`, so as a flex child of a dialog capped at 90vh anything past
   that height was simply unreachable — with a 20-file completion list on
   screen, the 待決定 section below it could not be scrolled to at all.
   One scroller for the whole panel, not one per list: two scrollable panes
   with no priority between them is what flattened the folder picker in the
   duplicate dialog into a sliver. */
.wrap { display: flex; flex-direction: column; flex: 1; min-height: 0;
  overflow-y: auto; }
.scopebar { background: var(--card); border: 1px solid #444; border-radius: 6px;
  color: var(--text); font-size: 12px; padding: 6px 10px; margin: 0 16px 8px;
  cursor: pointer; text-align: left; }
.calib { margin-left: 6px; font-size: 12px; }
.scopebar .chg { color: var(--accent); margin-left: 8px; }
/* Children never shrink: .wrap is the scroller, so anything taller than the
   dialog scrolls instead. With the default flex-shrink the picker block got
   squeezed below its content and its text spilled over the crf row. */
.wrap > * { flex-shrink: 0; }
.scopewrap { margin: 4px 16px 8px; padding: 12px 14px 2px; background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; }
.scanbtn { flex-shrink: 0; white-space: nowrap; }
.qmark.on { border-color: var(--accent); color: var(--accent); }
.params .unit + .lab, .params .radio + .lab { margin-left: 12px; }
.radio { display: inline-flex; align-items: center; gap: 4px; cursor: pointer;
  color: var(--text); }
.opts { display: flex; align-items: center; gap: 8px; padding: 8px 0;
  font-size: 12px; flex-wrap: wrap; }
.opts.params { margin: 0 16px 8px; padding: 10px 14px; background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; }
.lab { color: var(--dim); font-size: 11px; }
.unit { color: var(--dim); font-size: 11px; }
.spacer { flex: 1; }
.tip { color: var(--dim); font-size: 11px; line-height: 1.6; padding-bottom: 8px; }
.tip b { color: var(--text); }
.qmark { background: none; border: 1px solid #555; border-radius: 50%;
  color: var(--dim); width: 15px; height: 15px; font-size: 10px; padding: 0;
  cursor: pointer; margin-left: 4px; }
.helptbl { margin: 0 16px 10px; background: var(--card); border-radius: 6px;
  padding: 8px 10px; font-size: 11px; color: var(--text); }
.hh { color: var(--dim); margin-bottom: 5px; }
.helptbl table { border-collapse: collapse; width: 100%; }
.helptbl th, .helptbl td { text-align: left; padding: 2px 8px 2px 0;
  color: var(--dim); font-weight: normal; }
.helptbl tr.cur td { color: var(--accent); }
.hn { color: var(--dim); margin-top: 6px; line-height: 1.6; }
.hn b { color: var(--text); }
.prog { display: flex; align-items: center; gap: 8px; padding: 6px 16px;
  font-size: 12px; color: var(--text); }
.prog .bg { color: var(--dim); font-size: 11px; margin-left: auto; }
.bar { height: 4px; background: #2a2a3c; margin: 0 16px; border-radius: 2px; }
.fill { height: 100%; background: var(--accent); border-radius: 2px;
  transition: width .2s; }
.err { color: var(--danger); font-size: 12px; padding: 6px 16px; }
.done { padding: 8px 16px; font-size: 12px; color: var(--success); }
.doneline { background: none; border: none; padding: 0; cursor: pointer;
  color: var(--success); font-size: 12px; font-family: inherit; }
.doneline .lnk { margin-left: 8px; }
.okrow { color: var(--text); font-size: 11px; margin-top: 3px; }
.failrow { color: var(--danger); font-size: 11px; margin-top: 3px; }
/* no overflow of its own — the panel scrolls, see .wrap */
.results { padding: 0 16px 8px; }
.shead { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--dim); padding: 6px 0; }
.shead b { color: var(--accent); }
.hdr, .row { display: flex; align-items: center; gap: 8px; font-size: 11px;
  padding: 3px 0; }
.hdr { color: var(--dim); border-bottom: 1px solid #313148; }
.row { border-bottom: 1px solid #24243a; cursor: pointer; }
.row:hover { background: #23233a; }
.cb { width: 16px; flex: 0 0 auto; }
.c1 { width: 46px; text-align: right; flex: 0 0 auto; }
.c2 { width: 78px; flex: 0 0 auto; }
.c3 { width: 54px; flex: 0 0 auto; }
.c4 { width: 66px; text-align: right; flex: 0 0 auto; }
.c5 { width: 74px; text-align: right; flex: 0 0 auto; }
.c6 { width: 40px; flex: 0 0 auto; }
.num { color: var(--accent); }
.save { color: var(--success); }
.fn { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; }
.lnk:disabled { color: var(--dim); cursor: default; }
.empty { color: var(--dim); font-size: 12px; padding: 16px 0; }
.foot { display: flex; align-items: center; gap: 10px; padding: 8px 16px;
  font-size: 11px; }
.foot .dim { color: var(--dim); }
.sample { margin: 0 16px 8px; background: var(--card); border-radius: 6px;
  padding: 8px 10px; }
.btn.small { font-size: 11px; padding: 4px 10px; }
.mini { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 9px; cursor: pointer; }
.mini:hover { border-color: var(--accent); }
.mini.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.ck { color: var(--dim); }
.pend { margin: 8px 16px 12px; border-top: 1px solid #313148; padding-top: 8px; }
.phead { display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: var(--text); }
.phead b { color: var(--accent); }
.pnote { font-size: 11px; color: var(--dim); line-height: 1.6; margin: 4px 0 6px; }
.pnote b { color: var(--text); }
.prow { border-bottom: 1px solid #24243a; padding: 5px 0; }
.pline { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--text); }
.pline.sub { margin-top: 4px; }
.pn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 28%; }
.pn.new { color: var(--accent); }
.arrow { color: var(--dim); }
.tagk { font-size: 10px; border-radius: 3px; padding: 1px 7px; flex: 0 0 auto; }
.tagk.shrink { background: #1b1b33; color: var(--accent); }
.tagk.repair { background: #2a2010; color: #f9e2af; }
.tagk.conceal { background: #2a1520; color: #f38ba8; }
.regs { gap: 4px; flex-wrap: wrap; }
.chipbtn { background: transparent; border: 1px solid #555; border-radius: 999px;
  color: var(--text); font-size: 11px; padding: 1px 8px; cursor: pointer; }
.chipbtn:hover { border-color: var(--accent); color: var(--accent); }
.grow { color: var(--danger); }
.cmpnote { font-size: 11px; color: var(--dim); line-height: 1.6;
  margin-top: 6px; }
.cmpnote b { color: var(--text); }
.pcmp { margin-top: 8px; background: var(--card); border-radius: 6px;
  padding: 8px 10px; }
.verdict { font-size: 11px; color: var(--text); margin-top: 8px;
  line-height: 1.6; }
.verdict b { color: var(--accent); }
.dim { color: var(--dim); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
