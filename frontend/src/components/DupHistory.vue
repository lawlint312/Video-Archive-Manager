<script setup>
// The saved scans of one kind (video or image), newest first. Shared by both
// duplicate tabs, and by the convert dialog (`endpoint`, `unit`). Opening one hands its id back to the panel, which loads it
// the same way it loads the latest report.
//
// "還剩 N 組": for the duplicate scans, what was left the last time that report
// was opened (the list itself checks no file — opening it used to check every
// file of twenty reports, 51 s once); the convert history still counts now.
import { ref, onMounted } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const props = defineProps({
  kind: { type: String, required: true },          // 'video' | 'image'
  current: { type: String, default: null },        // id on screen, if any
  endpoint: { type: String, default: '/api/dedup/history' },
  unit: { type: String, default: '組' },           // what "found N" counts
})
const emit = defineEmits(['open', 'close'])

const entries = ref(null)
const keep = ref(20)
const error = ref('')
const busy = ref('')

async function load() {
  error.value = ''
  try {
    const r = await api(`${props.endpoint}?kind=${props.kind}`)
    entries.value = r.entries
    keep.value = r.keep
  } catch (e) { error.value = '讀取失敗：' + errText(e); entries.value = [] }
}
onMounted(load)

async function remove(e) {
  if (!confirm(`刪除 ${when(e.at)} 這筆掃描紀錄？\n\n只會刪除紀錄本身，不會動到任何影片或照片。`)) return
  busy.value = e.id
  try {
    await api(`${props.endpoint}/${props.kind}/${e.id}`, { method: 'DELETE' })
    entries.value = entries.value.filter(x => x.id !== e.id)
  } catch (err) { error.value = '刪除失敗：' + errText(err) }
  finally { busy.value = '' }
}

function when(t) {
  if (!t) return ''
  const d = new Date(t * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function scope(e) {
  const r = e.roots || []
  if (!r.length) return '（範圍不在你的權限內）'
  return r.slice(0, 3).join('、') + (r.length > 3 ? ` 等 ${r.length} 個資料夾` : '')
}
</script>

<template>
  <div class="hist">
    <div class="hhead">
      <Icon name="history" :size="14" />
      <b>歷史紀錄</b>
      <span class="dim">（保留最近 {{ keep }} 次掃描）</span>
      <span class="spacer"></span>
      <button class="btn ghost" @click="emit('close')">返回</button>
    </div>
    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="entries === null" class="dim pad">讀取中…</div>
    <div v-else-if="!entries.length" class="dim pad">還沒有任何掃描紀錄。</div>
    <div v-for="e in entries || []" :key="e.id" class="hrow"
         :class="{ cur: e.id === current }">
      <div class="when">
        {{ when(e.at) }}
        <span v-if="e.latest" class="tag new">最新</span>
        <span v-if="e.partial" class="tag warn">中途停止</span>
        <span v-if="e.id === current" class="tag on">目前顯示</span>
      </div>
      <div class="what">
        <span class="sc" :title="(e.roots || []).join('\n')">{{ scope(e) }}</span>
        <span v-if="kind === 'video' && e.tier2" class="tag">含 Tier 2</span>
        <span v-if="kind === 'image' && e.max_distance != null" class="tag">門檻 {{ e.max_distance }}</span>
        <span v-for="t in e.tags || []" :key="t" class="tag">{{ t }}</span>
      </div>
      <!-- The duplicate lists say what was left when the report was last
           opened (`remaining_at`): the list itself checks no file, so it
           opens at once instead of checking twenty reports on the NAS. -->
      <div class="cnt" :title="e.remaining_at ? `${when(e.remaining_at)} 開啟時的狀況` : ''">
        找到 {{ e.groups }} {{ unit }}
        <template v-if="e.remaining == null"> · <span class="never">還沒開啟過</span></template>
        <template v-else> ·
          <template v-if="e.remaining_at">上次開啟時</template>
          <b v-if="e.remaining" class="left">還剩 {{ e.remaining }} {{ unit }}</b>
          <span v-else class="done">已處理完</span>
        </template>
      </div>
      <div class="acts">
        <button class="btn xs" :disabled="!!busy" @click="emit('open', e.id)">開啟</button>
        <button class="btn ghost xs del" :disabled="!!busy" title="只刪除紀錄，不動任何檔案"
                @click="remove(e)">刪除</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hist { display: flex; flex-direction: column; gap: 6px; padding: 10px 16px; }
.hhead { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-bottom: 4px; }
.spacer { flex: 1; }
.dim { color: var(--dim); font-size: 12px; }
.pad { padding: 16px 0; }
.err { color: var(--danger); font-size: 12px; }
.hrow { display: grid; grid-template-columns: 190px 1fr auto auto; gap: 12px;
  align-items: center; padding: 8px 10px; border: 1px solid #313148;
  border-radius: 6px; font-size: 12px; }
.hrow.cur { border-color: var(--accent); }
.when { white-space: nowrap; }
.what { min-width: 0; display: flex; gap: 6px; align-items: center; }
.sc { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
.cnt { white-space: nowrap; color: var(--dim); }
.left { color: #f9e2af; font-weight: normal; }
.done { color: var(--success); }
.never { color: var(--dim); }
.acts { display: flex; gap: 4px; }
.tag { font-size: 10px; border-radius: 3px; padding: 1px 6px; background: #2a2a3a;
  color: var(--dim); margin-left: 4px; white-space: nowrap; }
.tag.new { background: #1b1b33; color: var(--accent); }
.tag.warn { background: #2a2010; color: #f9e2af; }
.tag.on { background: #162416; color: var(--success); }
.btn.xs { padding: 3px 10px; font-size: 12px; }
.acts .btn.del { background: #5a2020; color: var(--danger); border: none; }
</style>
