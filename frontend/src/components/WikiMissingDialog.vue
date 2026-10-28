<script setup>
/**
 * Releases the wiki lists that are not in this character's folder.
 *
 * A dialog rather than an expansion of the comparison panel, because of the
 * scale: one real page listed 750 releases of which 695 were missing, and
 * Japanese titles need the width. The panel stays a summary.
 *
 * Unlike the other numbers in that panel, this one cannot filter the video
 * grid — these are films the user does not have, so there is no card to show.
 * What they want is the catalogue number, to go and search with, so copying it
 * is the whole interaction.
 */
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { sectionName } from '../wikisections'
import Icon from './Icon.vue'

const props = defineProps({
  root: { type: String, required: true },
  name: { type: String, default: '' },
  section: { type: String, default: '' },   // '' = every section
})
const emit = defineEmits(['close'])

const works = ref([])
const loading = ref(true)
const error = ref('')
const stale = ref(false)
const q = ref('')
const copied = ref('')
// Films whose footage the user already owns in another (AVBase's relations):
// the original it was cut from, or an uncut compilation. Hidden by default —
// downloading them would add nothing — but countable and one click away.
const hideCovered = ref(true)
const coveredCount = computed(() => works.value.filter(w => w.covered_by).length)

const shown = computed(() => {
  const s = q.value.trim().toLowerCase()
  const list = hideCovered.value ? works.value.filter(w => !w.covered_by) : works.value
  if (!s) return list
  return list.filter(w =>
    w.video_id.toLowerCase().includes(s) || w.title.toLowerCase().includes(s))
})

// 695 rows is more than a browser should build at once for a list nobody
// scrolls to the end of — the same reason the video grid renders in batches.
const BATCH = 120
const limit = ref(BATCH)
const visible = computed(() => shown.value.slice(0, limit.value))

async function load() {
  try {
    const d = await api(`/api/wiki/missing?root=${encodeURIComponent(props.root)}` +
                        (props.section ? `&section=${encodeURIComponent(props.section)}` : ''))
    works.value = d.works
    stale.value = d.stale
  } catch (e) {
    error.value = '讀取失敗：' + errText(e)
  } finally { loading.value = false }
}

async function copy(text, mark) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = mark
    setTimeout(() => { if (copied.value === mark) copied.value = '' }, 1200)
  } catch {
    // clipboard is blocked over plain HTTP on some browsers; a prompt is ugly
    // but it still lets the number be copied by hand
    prompt('複製：', text)
  }
}

const allIds = computed(() =>
  shown.value.map(w => w.video_id).filter(Boolean).join('\n'))

onMounted(load)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="film" :size="16" />
          尚未收入 · {{ name }}<template v-if="section"> · {{ sectionName(section) }}</template>
        </span>
        <span class="spacer"></span>
        <button class="btn ghost" @click="emit('close')">
          <Icon name="close" :size="14" /></button>
      </div>

      <div v-if="loading" class="msg">載入中…</div>
      <div v-else-if="error" class="msg err">{{ error }}</div>
      <div v-else-if="!works.length" class="msg">
        匯入的頁面上的作品你都有了。
      </div>

      <template v-else>
        <!-- Imported before the release list was stored: the numbers are
             there, the titles are not. Says what to do about it. -->
        <div v-if="stale" class="note">
          這位角色的 wiki 資料是舊版匯入的，只存了番號沒有存片名。
          到「角色查詢 → WIKI 匯入」重新丟一次那個頁面就會有了。
        </div>
        <div class="bar">
          <input v-model="q" class="find" placeholder="搜尋番號或片名…" />
          <span class="cnt">{{ shown.length }} 部</span>
          <label v-if="coveredCount" class="cov"
                 title="AVBase 的收錄關係：這些片的內容在你已有的另一部裡（原片，或完整收錄的總集編）">
            <input type="checkbox" v-model="hideCovered" />
            隱藏已有原片的 {{ coveredCount }} 部</label>
          <span class="spacer"></span>
          <button class="btn ghost sm ib" @click="copy(allIds, '__all__')">
            <Icon :name="copied === '__all__' ? 'check' : 'copy'" :size="13" />
            {{ copied === '__all__' ? '已複製' : '複製全部番號' }}
          </button>
        </div>

        <div class="list">
          <!-- Clicking anywhere on the row copies the number: that is the one
               thing this list exists to hand over. -->
          <div v-for="w in visible" :key="w.video_id + w.date" class="row"
               :class="{ hit: copied === w.video_id }"
               :title="w.video_id ? '點一下複製番號' : '這一部 wiki 沒有記番號'"
               @click="w.video_id && copy(w.video_id, w.video_id)">
            <span class="date">{{ w.date }}</span>
            <span class="vid" :class="{ none: !w.video_id }">
              {{ w.video_id || '—' }}</span>
            <span class="title">{{ w.title }}</span>
            <span v-if="w.covered_by" class="covby">已有原片 {{ w.covered_by }}</span>
            <span v-if="!section" class="sec">{{ sectionName(w.section) }}</span>
            <Icon v-if="copied === w.video_id" name="check" :size="12" class="ok" />
          </div>
          <div v-if="limit < shown.length" class="more"
               @click="limit += BATCH">
            顯示 {{ visible.length }} / {{ shown.length }} · 點此載入更多
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; width: min(880px, 94vw);
  max-height: 88vh; max-height: 88dvh;
  display: flex; flex-direction: column; padding: 14px 16px; gap: 10px; }
.dhead { display: flex; align-items: center; gap: 8px; }
.spacer { flex: 1; }
.msg { color: var(--dim); padding: 30px 10px; text-align: center;
  line-height: 1.9; }
.msg.err { color: var(--danger); }
.bar { display: flex; align-items: center; gap: 10px; }
.find { flex: 0 1 280px; font-size: 12px; padding: 5px 8px; }
.cnt { color: var(--dim); font-size: 11px; }
/* The scroller. `min-height: 0` for the usual reason — without it a flex child
   refuses to shrink and the dialog's max-height does nothing. */
.list { flex: 1; min-height: 0; overflow-y: auto;
  border: 1px solid var(--line); border-radius: 6px; }
.row { display: flex; align-items: baseline; gap: 10px; padding: 4px 10px;
  font-size: 12px; cursor: pointer; border-bottom: 1px solid #22222f; }
.row:hover { background: #1b1b2a; }
.row.hit { background: #162416; }
.date { color: var(--dim); font-size: 10px; flex-shrink: 0;
  font-variant-numeric: tabular-nums; width: 74px; }
.vid { color: #f9e2af; flex-shrink: 0; width: 108px; }
.vid.none { color: #45455a; }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.sec { color: var(--dim); font-size: 10px; flex-shrink: 0; }
.covby { color: var(--success); font-size: 10px; flex-shrink: 0; }
.cov { color: var(--dim); font-size: 11px; display: inline-flex; align-items: center;
  gap: 4px; cursor: pointer; }
.note { color: #f9e2af; font-size: 11px; line-height: 1.6; }
.ok { color: var(--success); flex-shrink: 0; }
.more { text-align: center; color: var(--dim); font-size: 11px;
  padding: 10px; cursor: pointer; }
.more:hover { color: var(--accent); }
.btn.sm { padding: 3px 9px; font-size: 11px; }
</style>
