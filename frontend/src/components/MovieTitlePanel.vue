<script setup>
/**
 * Fetch film titles from javdatabase for the catalogue numbers under the
 * chosen characters.
 *
 * No preview step, unlike JAV個資查詢: a catalogue number identifies exactly
 * one film, so there is no identity to get wrong and nothing to rule on. The
 * titles are stored against the number rather than the file, so every copy of
 * the same film gets one and a rename cannot lose it.
 *
 * DMM first, for the original Japanese; javdatabase's machine-translated
 * English only where DMM has nothing (Prestige left FANZA; FC2, HEYZO and
 * amateur numbers were never on it). Anything typed by hand outranks both.
 */
import { ref, computed } from 'vue'
import { api, errText } from '../api'
import { setStatus, setBusy } from '../store'
import CharPicker from './CharPicker.vue'
import Icon from './Icon.vue'

const emit = defineEmits(['close'])

// JavDialog disables its close button while this is true: closing on
// a scan in progress abandons a job with no way back to it.
const busy = computed(() => scanning.value)
defineExpose({ busy })

const selected = ref([])
const scanning = ref(false)
const progress = ref('')
const result = ref(null)
const error = ref('')
const pickerOpen = ref(true)
const showMissing = ref(false)
// 「同時下載封面」: DMM's sleeve beside the videos (core/dmmcover.py)
const covers = ref(false)
const showNoCover = ref(false)

const found = computed(() => result.value?.found || [])
// where the shown title came from: DMM just now, one already Japanese
// (wiki / NFO / DMM before), hand-typed, or javdatabase's English
const SOURCE = { dmm: 'DMM 日文', ja: '日文', manual: '手打', en: '英文' }
const missing = computed(() => result.value?.missing || [])

async function scan() {
  if (!selected.value.length) { error.value = '請至少選擇一位角色'; return }
  error.value = ''
  scanning.value = true
  result.value = null
  pickerOpen.value = false
  setBusy('JAV片名查詢：啟動中…')
  try {
    const { job_id } = await api('/api/movietitle/scan', {
      method: 'POST',
      body: JSON.stringify({ names: selected.value, covers: covers.value }),
    })
    while (true) {
      await new Promise(r => setTimeout(r, 700))
      const j = await api(`/api/dedup/job/${job_id}`)
      progress.value = j.progress || ''
      if (j.progress) setBusy('JAV片名查詢：' + j.progress)
      if (j.status === 'done') {
        result.value = j.result
        setStatus(`片名查詢完成：${j.result.found.length} / ${j.result.total} 部`
                  + (j.result.dmm_new ? `（DMM 日文 ${j.result.dmm_new} 部）` : '')
                  + (j.result.covers ? `，新封面 ${j.result.covers.new.length} 部` : ''),
                  j.result.found.length ? 'ok' : 'warn')
        break
      }
      if (j.status === 'error') { error.value = j.error || '查詢失敗'; break }
    }
  } catch (e) {
    error.value = '查詢失敗：' + errText(e)
  } finally {
    scanning.value = false
    progress.value = ''
    setBusy('')
  }
}
</script>

<template>
  <div class="panel">

      <!-- No "choose characters" toggle above the chooser: you opened this
           to choose, and a button captioned like an instruction while the
           list is already below it describes a screen that is not there.
           The collapsed bar appears only after a scan, because the picker and
           the results are both scrollers and fight for height — the same
           arrangement the convert dialog uses. -->
      <button v-if="!pickerOpen" class="scopebar" @click="pickerOpen = true">
        <Icon name="chevronRight" :size="13" />
        範圍：已選 {{ selected.length }} 位角色
        <span class="chg">重新選擇</span>
      </button>

      <!-- `v-if`, not `v-show`: CharPicker fetches each character's
           completion state on mount, so re-opening it after a scan has
           to re-mount or the "未處理" counts still show the work you
           just did as outstanding. -->
      <div v-if="pickerOpen" class="scope">
        <CharPicker mode="title" :disabled="scanning"
                    @update:selected="v => selected = v" />
        <div class="row">
          <button class="btn" :disabled="scanning || !selected.length" @click="scan">
            <Icon name="search" :size="14" /> 開始查詢
          </button>
          <label class="covers" title="存在影片旁邊：番號-poster.jpg 是直式的正面、番號-fanart.jpg 是橫式的整張封套。卡片依分類的比例選用。已經有的不下載、不覆蓋">
            <input type="checkbox" v-model="covers" :disabled="scanning" />
            同時下載封面（直式 poster、橫式 fanart）
          </label>
          <span class="hint">
            先查 <b>DMM</b> 的日文原名，查不到才用 javdatabase 的英文。<b>查到直接套用</b>；
            你手打的片名不會被蓋掉，已有日文的不再查。
          </span>
        </div>
      </div>

      <div v-if="scanning" class="prog">
        <Icon name="spinner" :size="13" class="spin" /> {{ progress || '查詢中…' }}
        <span class="hint">每個番號約 2～5 秒；可以關掉這個視窗，狀態列會繼續顯示進度</span>
      </div>
      <div v-if="error" class="err">{{ error }}</div>

      <!-- Hidden while the picker is open. Both panes scroll, and with no
           priority between them the browser splits the height by content —
           an 84-group report left the 745-row folder list a visible sliver.
           You are either setting up a run or reviewing the last one. -->
      <div v-if="result && !pickerOpen" class="results">
        <div class="bar">
          共 {{ result.total }} 個番號 · <b>已套用 {{ found.length }}</b>
          <span v-if="result.dmm_new"> · 其中 DMM 日文 <b>{{ result.dmm_new }}</b></span>
          <span v-if="missing.length">
            · 查無片名 <b class="dim">{{ missing.length }}</b>
            <button class="lnk" @click="showMissing = !showMissing">
              {{ showMissing ? '收起' : '看清單' }}
            </button>
          </span>
        </div>

        <div v-if="result.covers" class="bar">
          封面：<b>新下載 {{ result.covers.new.length }}</b>
          <span v-if="result.covers.javdatabase?.length"
                title="DMM 商品目錄查不到的片：改用 javdatabase 頁面上的連結，從 DMM 舊圖庫或 MGS 圖庫下載">
            （其中 {{ result.covers.javdatabase.length }} 部經 javdatabase 連結）</span>
          · 已經有 {{ result.covers.had }}
          <span v-if="result.covers.none.length">
            · 找不到 <b class="dim">{{ result.covers.none.length }}</b>
            <button class="lnk" @click="showNoCover = !showNoCover">
              {{ showNoCover ? '收起' : '看清單' }}
            </button>
          </span>
          <span v-if="result.covers.failed.length">
            · 失敗 <b class="warn">{{ result.covers.failed.length }}</b>
          </span>
        </div>
        <div v-if="showNoCover && result.covers?.none.length" class="missing">
          <!-- what neither DMM nor javdatabase's links have: mostly MGS
               amateur (ARA, NTK) and uncensored numbers. mgstage.com itself
               refuses Taiwan, so these stay without a cover -->
          <div class="hint">DMM 與 javdatabase 都沒有（多半是 MGS 素人系與無碼；MGS 網站不對台灣開放）：</div>
          {{ result.covers.none.join('、') }}
        </div>
        <div v-if="result.covers?.failed.length" class="missing">
          <div v-for="f in result.covers.failed" :key="f.id">{{ f.id }}：{{ f.error }}</div>
        </div>

        <div v-if="showMissing && missing.length" class="missing">
          <!-- about a fifth of catalogue numbers have no page at all, and FC2
               amateur numbers almost never do -->
          {{ missing.map(m => m.id).join('、') }}
        </div>

        <!-- not fatal: javdatabase answered for the rest of the run -->
        <div v-if="result.dmm_error" class="dmmerr">
          <Icon name="warn" :size="12" />
          這次連不上 DMM（{{ result.dmm_error }}），改用 javdatabase 的英文；之後再查一次就會補上日文。
        </div>

        <div class="donehint">
          <Icon name="checked" :size="12" />
          已寫入。要修改某一部的片名，播放那部影片時，在播放器下方的「片名」欄直接改（電腦版）。
        </div>

        <div class="list">
          <div v-for="f in found" :key="f.id" class="row-item">
            <b>{{ f.id }}</b>
            <span class="src" :class="f.source">{{ SOURCE[f.source] || '' }}</span>
            <span class="t">{{ f.title }}</span>
          </div>
        </div>
      </div>
    
  
  </div>
</template>

<style scoped>
.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 8px 0; background: none; border: none;
  border-bottom: 1px solid #313148; color: var(--dim); font-size: 12px;
  cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
/* The modal shell lives in JavDialog.vue now; this file is
   one tab of it. */
.panel { display: contents; }
.spacer { flex: 1; }
.hint { color: var(--dim); font-size: 11px; }
.scope { background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; padding: 12px 14px; }
.row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
/* the hint beside it wraps, the button does not */
.row .btn { flex: 0 0 auto; white-space: nowrap; }
.prog { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.err { color: var(--danger, #e66); font-size: 12px; }
.results { display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
.bar { font-size: 12px; }
.bar b { color: var(--accent); }
.bar b.dim { color: var(--dim); }
.bar b.warn { color: #e9b949; }
.covers { display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
          white-space: nowrap; cursor: pointer; flex: 0 0 auto; }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; margin-left: 4px; }
.donehint { display: flex; align-items: center; gap: 5px; font-size: 11px;
  color: #7ddba0; }
.hint b { color: #e9b949; }
.missing { font-size: 11px; color: var(--dim); word-break: break-all;
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; }
.list { overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.row-item { display: flex; gap: 10px; font-size: 12px; padding: 3px 0;
  border-bottom: 1px solid #1f1f2e; }
.row-item b { flex: 0 0 110px; color: var(--accent); }
.src { flex: 0 0 auto; font-size: 10px; border-radius: 6px; padding: 0 6px;
  align-self: center; background: #ffffff14; color: var(--dim); }
.src.dmm { background: #1e3a28; color: var(--success); }
.src.manual { background: #2a2a4a; color: var(--accent); }
.dmmerr { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #f9e2af; }
.t { color: #bac2de; }
</style>
