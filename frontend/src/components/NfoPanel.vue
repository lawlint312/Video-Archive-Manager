<script setup>
/**
 * NFO files beside the videos (Kodi / Jellyfin / Emby), both directions.
 *
 *   讀入 VAM   fills the titles, release dates and co-stars VAM does not know
 *              yet, from the files already there. Writes only VAM's tables.
 *   寫出 NFO   merges VAM's titles, dates and cast into each video's .nfo —
 *              only those fields; the rest of a file stays as it is. Every
 *              changed file is backed up and the last batch can be undone.
 *
 * Both are a preview first and a second press to write (as the wiki import
 * is), and the write works the plan out again on the server rather than
 * trusting this page's copy of it.
 */
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { setStatus, setBusy } from '../store'
import { patiently } from '../jobfollow'
import CharPicker from './CharPicker.vue'
import Icon from './Icon.vue'

const mode = ref('import')           // import | export
const selected = ref([])
const pickerOpen = ref(true)
const running = ref('')              // '' | scan | apply
const progress = ref('')
const result = ref(null)             // the preview
const applied = ref(null)            // what the write did
const error = ref('')
const last = ref(null)               // the batch that can be undone
const limit = ref(300)

const busy = computed(() => !!running.value)
defineExpose({ busy })

const TITLE_LANG = { ja: '日', zh: '中', en: '英', '': '' }
const EXPORT_FIELD = { title: '片名', originaltitle: '原片名', premiered: '發行日',
                       year: '年份', num: '番號', actor: '演員' }
const STATUS = { new: '新建', changed: '更新', error: '無法處理' }

function fillLabel(f) {
  if (f.field === 'title') return `片名${TITLE_LANG[f.lang] ? `（${TITLE_LANG[f.lang]}）` : ''}`
  if (f.field === 'released') return '發行日'
  return '共演'
}

async function loadLast() {
  try {
    const r = await api('/api/nfo/last')
    last.value = r.batch ? r : null
  } catch { last.value = null }
}
onMounted(loadLast)

function setMode(m) {
  if (busy.value || mode.value === m) return
  mode.value = m
  result.value = null
  applied.value = null
  error.value = ''
  pickerOpen.value = true
}

async function follow(jobId, label) {
  for (;;) {
    await new Promise(r => setTimeout(r, 700))
    const j = await patiently(() => api(`/api/jobs/${jobId}`))
    progress.value = j.progress || ''
    if (j.progress) setBusy(`${label}：${j.progress}`)
    if (j.status === 'running') continue
    if (j.status === 'error') throw { detail: j.error || '執行失敗' }
    return j.result
  }
}

async function run(kind) {
  if (!selected.value.length) { error.value = '請至少選擇一位角色'; return }
  error.value = ''
  running.value = kind
  if (kind === 'scan') { result.value = null; applied.value = null; limit.value = 300 }
  pickerOpen.value = false
  const label = kind === 'scan' ? 'NFO 比對'
    : mode.value === 'import' ? '匯入 NFO' : '寫入 NFO'
  setBusy(`${label}：啟動中…`)
  try {
    const { job_id } = await api(`/api/nfo/${kind}`, {
      method: 'POST', body: JSON.stringify({ names: selected.value, mode: mode.value }),
    })
    const r = await follow(job_id, label)
    if (kind === 'scan') {
      result.value = r
    } else {
      applied.value = r
      setStatus(summary.value, (r.errors || []).length ? 'warn' : 'ok')
      if (mode.value === 'export') loadLast()
    }
  } catch (e) {
    error.value = `${label}失敗：${errText(e)}`
  } finally {
    running.value = ''
    progress.value = ''
    setBusy('')
  }
}

function apply() {
  if (mode.value === 'export') {
    const c = result.value.counts
    if (!confirm(`寫入 NFO：新建 ${c.new} 個、更新 ${c.changed} 個？\n\n`
                 + '只改片名、原片名、發行日、演員、番號；檔案裡其他內容不動。'
                 + '\n更新前會先備份，寫完可以「復原上一批」。')) return
  }
  run('apply')
}

async function undo() {
  const l = last.value
  if (!l || !confirm(`復原上一批寫入（新建 ${l.created} 個、更新 ${l.updated} 個）？\n\n`
                     + '新建的檔會移除，更新的檔換回原本的內容。'
                     + '\n之後被別的程式（例如 Jellyfin）改過的檔會保留，不會蓋掉。')) return
  running.value = 'undo'
  error.value = ''
  try {
    const r = await api('/api/nfo/undo', { method: 'POST' })
    const skipped = r.skipped.length ? `，${r.skipped.length} 個之後被改過而保留` : ''
    setStatus(`已復原：換回 ${r.restored} 個、移除 ${r.removed} 個${skipped}`,
              r.skipped.length ? 'warn' : 'ok')
    applied.value = null
    result.value = null
    pickerOpen.value = true
    await loadLast()
  } catch (e) { error.value = '復原失敗：' + errText(e) }
  finally { running.value = '' }
}

const rows = computed(() => result.value?.rows || [])
const items = computed(() => result.value?.items || [])
const canApply = computed(() => mode.value === 'import'
  ? rows.value.length > 0
  : !!result.value && (result.value.counts.new + result.value.counts.changed) > 0)

const summary = computed(() => {
  const a = applied.value
  if (!a) return ''
  if (mode.value === 'import') {
    return `已匯入：片名 ${a.titles}、發行日 ${a.released}、共演 ${a.costars}`
  }
  const err = a.errors.length ? `，${a.errors.length} 個失敗` : ''
  return `已寫入 NFO：新建 ${a.created}、更新 ${a.updated}${err}`
})

function short(p) { return p.split(/[\\/]/).slice(-2).join('\\') }
</script>

<template>
  <div class="panel">
    <div class="modes">
      <button class="seg" :class="{ on: mode === 'import' }" :disabled="busy"
              @click="setMode('import')">
        <Icon name="down" :size="13" /> 讀入 VAM
      </button>
      <button class="seg" :class="{ on: mode === 'export' }" :disabled="busy"
              @click="setMode('export')">
        <Icon name="up" :size="13" /> 寫出 NFO
      </button>
      <span class="spacer"></span>
      <button v-if="mode === 'export' && last" class="btn ghost sm" :disabled="busy"
              @click="undo">
        <Icon name="history" :size="13" /> 復原上一批（{{ last.count }} 個檔）
      </button>
    </div>

    <div class="intro">
      <template v-if="mode === 'import'">
        讀取影片旁同名的 <b>.nfo</b>（Kodi／Jellyfin／Emby 的格式），把 VAM 還沒有的
        <b>片名、發行日、共演</b>補上。<b>只補空的</b>：你手打的、從 wiki 匯入的片名不會被換掉。
        只寫 VAM 自己的資料庫，不動任何檔案。
      </template>
      <template v-else>
        把 VAM 的<b>片名、原片名、發行日、演員、番號</b>寫進每部影片旁的 <b>.nfo</b>。
        已經有 NFO 的會<b>合併</b>：只更新這幾欄，簡介、海報、串流資訊等原樣保留；
        演員只加不刪。更新前先備份，寫完可以復原上一批。
        <br />提醒：Jellyfin 若開了「把中繼資料存成 NFO」，它重新整理時可能會改寫這些檔。
      </template>
    </div>

    <button v-if="!pickerOpen" class="scopebar" @click="pickerOpen = true">
      <Icon name="chevronRight" :size="13" />
      範圍：已選 {{ selected.length }} 位角色
      <span class="chg">重新選擇</span>
    </button>
    <div v-if="pickerOpen" class="scope">
      <CharPicker mode="plain" :initial="selected" :disabled="busy"
                  @update:selected="v => selected = v" />
      <div class="row">
        <button class="btn" :disabled="busy || !selected.length" @click="run('scan')">
          <Icon name="search" :size="14" /> 預覽
        </button>
        <span class="hint">預覽不會寫入任何東西。</span>
      </div>
    </div>

    <div v-if="running" class="prog">
      <Icon name="spinner" :size="13" class="spin" />
      {{ progress || (running === 'scan' ? '比對中…' : running === 'undo' ? '復原中…' : '寫入中…') }}
    </div>
    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="applied" class="done">
      <Icon name="checked" :size="12" /> {{ summary }}
      <div v-for="e in applied.errors || []" :key="e.path" class="eline">
        {{ short(e.path) }}：{{ e.error }}</div>
    </div>

    <!-- 讀入 VAM -->
    <div v-if="result && !pickerOpen && mode === 'import'" class="results">
      <div class="bar">
        {{ result.videos }} 部影片 · 找到 <b>{{ result.found }}</b> 個 NFO ·
        可補上 <b>{{ rows.length + (result.more || 0) }}</b> 部的資料
        <span class="spacer"></span>
        <button class="btn sm" :disabled="busy || !canApply || !!applied" @click="apply">
          <Icon name="check" :size="13" /> 寫入 VAM
        </button>
      </div>
      <details v-if="result.unmatched.length" class="more">
        <summary>{{ result.unmatched.length }} 個演員名對不到任何角色（不處理）</summary>
        <div class="names">
          <span v-for="u in result.unmatched" :key="u.name">{{ u.name }}<i>×{{ u.count }}</i></span>
        </div>
      </details>
      <details v-if="result.errors.length" class="more">
        <summary class="bad">{{ result.errors.length }} 個 NFO 讀不懂（略過）</summary>
        <div v-for="e in result.errors" :key="e.path" class="eline">
          {{ short(e.path) }}：{{ e.error }}</div>
      </details>
      <div v-if="!rows.length" class="hint">沒有要補的：NFO 裡的資料 VAM 都已經有了。</div>
      <div class="list">
        <div v-for="r in rows.slice(0, limit)" :key="r.path" class="item">
          <div class="who"><b>{{ r.name }}</b><span class="char">{{ r.char }}</span></div>
          <div class="fills">
            <span v-for="(f, i) in r.fills" :key="i" class="fill">
              <em>{{ fillLabel(f) }}</em> {{ f.value }}</span>
          </div>
        </div>
        <button v-if="rows.length > limit" class="lnk" @click="limit += 300">
          再顯示 {{ Math.min(300, rows.length - limit) }} 筆（共 {{ rows.length }}）</button>
        <div v-if="result.more" class="hint">另有 {{ result.more }} 筆沒有列出，寫入時一樣會處理。</div>
      </div>
    </div>

    <!-- 寫出 NFO -->
    <div v-if="result && !pickerOpen && mode === 'export'" class="results">
      <div class="bar">
        {{ result.videos }} 部影片 ·
        新建 <b>{{ result.counts.new }}</b> ·
        更新 <b>{{ result.counts.changed }}</b> ·
        沒變 {{ result.counts.same }}
        <template v-if="result.counts.error"> · <b class="bad">無法處理 {{ result.counts.error }}</b></template>
        <span class="spacer"></span>
        <button class="btn sm" :disabled="busy || !canApply || !!applied" @click="apply">
          <Icon name="check" :size="13" /> 寫入 NFO
        </button>
      </div>
      <div v-if="!canApply && !result.counts.error" class="hint">
        全部都已經是最新的，不需要寫入。</div>
      <div class="list">
        <div v-for="it in items.slice(0, limit)" :key="it.path" class="item">
          <div class="who">
            <span class="st" :class="it.status">{{ STATUS[it.status] }}</span>
            <b>{{ short(it.nfo) }}</b>
          </div>
          <div class="fills">
            <template v-if="it.status === 'error'"><span class="bad">{{ it.error }}</span></template>
            <span v-else v-for="f in it.fields" :key="f" class="fill"><em>{{ EXPORT_FIELD[f] || f }}</em></span>
          </div>
        </div>
        <button v-if="items.length > limit" class="lnk" @click="limit += 300">
          再顯示 {{ Math.min(300, items.length - limit) }} 筆（共 {{ items.length }}）</button>
        <div v-if="result.more" class="hint">另有 {{ result.more }} 筆沒有列出，寫入時一樣會處理。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* `contents` so JavDialog's body is the only scroller */
.panel { display: contents; }
.modes { display: flex; align-items: center; gap: 6px; }
.seg { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
  border-radius: 8px; border: 1px solid #ffffff1a; background: none;
  color: var(--dim); font: inherit; font-size: 12px; cursor: pointer; }
.seg.on { color: var(--text); background: var(--card); border-color: var(--accent); }
.seg:disabled { cursor: default; }
.spacer { flex: 1; }
.intro { color: var(--dim); font-size: 11px; line-height: 1.8;
  background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; padding: 10px 14px; }
.intro b { color: var(--text); }
.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 8px 0; background: none; border: none;
  border-bottom: 1px solid #313148; color: var(--dim); font-size: 12px;
  cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
.scope { background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; padding: 12px 14px; }
.row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.hint { color: var(--dim); font-size: 11px; }
.prog { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.spin { animation: nfospin 1s linear infinite; }
@keyframes nfospin { to { transform: rotate(360deg); } }
.err { color: var(--danger, #e66); font-size: 12px; }
.done { color: var(--success); font-size: 12px; background: #162416;
  border: 1px solid var(--success); border-radius: 6px; padding: 7px 10px; }
.eline { color: var(--danger, #e66); font-size: 11px; margin-top: 3px; word-break: break-all; }
.results { display: flex; flex-direction: column; gap: 8px; }
.bar { display: flex; align-items: center; gap: 6px; font-size: 12px; flex-wrap: wrap; }
.bar b { color: var(--accent); }
.bad { color: var(--danger, #e66) !important; }
.btn.sm { font-size: 12px; padding: 4px 12px; }
.more { font-size: 11px; }
.more summary { color: var(--dim); cursor: pointer; }
.names { display: flex; flex-wrap: wrap; gap: 4px 10px; padding: 4px 0 0 14px; color: var(--text); }
.names i { color: var(--dim); font-style: normal; margin-left: 2px; }
.list { display: flex; flex-direction: column; gap: 4px; }
.item { display: flex; gap: 12px; font-size: 12px; padding: 4px 0;
  border-bottom: 1px solid #1f1f2e; align-items: baseline; }
.who { flex: 0 0 38%; min-width: 0; display: flex; gap: 8px; align-items: baseline;
  overflow: hidden; }
.who b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.char { color: var(--dim); font-size: 11px; white-space: nowrap; }
.fills { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; gap: 4px 10px; color: #bac2de; }
.fill em { font-style: normal; color: var(--accent); margin-right: 3px; font-size: 11px; }
.st { font-size: 10px; border-radius: 6px; padding: 0 6px; flex: 0 0 auto; }
.st.new { background: #1e3a28; color: var(--success); }
.st.changed { background: #2a2a4a; color: var(--accent); }
.st.error { background: #3a1e24; color: var(--danger); }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; align-self: flex-start; }
@media (max-width: 720px) {
  .item { flex-direction: column; gap: 2px; }
  .who { flex: none; }
}
</style>
