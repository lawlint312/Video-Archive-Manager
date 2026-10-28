<script setup>
import { ref, onMounted } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const props = defineProps({
  char: { type: Object, required: true },   // merged entry with .paths
})
const emit = defineEmits(['close', 'done'])

const dstPath = ref(props.char.paths[0])
const plan = ref(null)
const summary = ref(null)
const loading = ref(false)
const running = ref(false)
const progress = ref(null)
const error = ref('')
const capacity = ref(null)   // { crossVolume, need, free, fits }
const warnings = ref([])

function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(1) + ' MB'
}
const sources = () => props.char.paths.filter(p => p !== dstPath.value)

async function buildPlan() {
  loading.value = true; error.value = ''; plan.value = null
  try {
    const all = []
    const sum = { move: 0, rename: 0, delete: 0, bytes: 0 }
    // capacity is per source folder: each one may sit on a different volume
    const cap = { crossVolume: 0, need: 0, free: -1, fits: true }
    for (const src of sources()) {
      const d = await api('/api/character/merge/plan', {
        method: 'POST', body: JSON.stringify({ src, dst: dstPath.value }),
      })
      all.push({ src, plan: d.plan, summary: d.summary })
      for (const k of Object.keys(sum)) sum[k] += d.summary[k]
      if (!d.same_volume) {
        cap.crossVolume++
        cap.need += d.summary.bytes
        cap.free = d.free
        if (!d.fits) cap.fits = false
      }
    }
    plan.value = all
    summary.value = sum
    capacity.value = cap
  } catch (e) {
    error.value = '分析失敗：' + errText(e)
  } finally { loading.value = false }
}

async function run() {
  if (capacity.value && !capacity.value.fits) {
    alert(`目標磁碟空間不足

需要 ${fmtSize(capacity.value.need)}，剩餘 ${fmtSize(capacity.value.free)}`)
    return
  }
  if (!confirm(`確定整合？\n\n${summary.value.move + summary.value.rename} 個檔案將移動到：\n${dstPath.value}\n\n${summary.value.delete} 個重複檔案將被刪除`)) return
  running.value = true
  warnings.value = []
  try {
    for (const group of plan.value) {
      const { job_id } = await api('/api/character/merge', {
        method: 'POST',
        body: JSON.stringify({ src: group.src, dst: dstPath.value, plan: group.plan }),
      })
      while (true) {
        await new Promise(r => setTimeout(r, 400))
        const j = await api(`/api/character/merge/job/${job_id}`)
        if (j.progress && typeof j.progress === 'object') progress.value = j.progress
        if (j.status === 'done') {
          // the source folder could not be tidied away — say what to do about
          // it rather than leaving an unexplained folder on the NAS
          if (j.result?.leftover) warnings.value.push(j.result.leftover)
          if (j.result?.errors?.length) warnings.value.push(...j.result.errors)
          break
        }
        if (j.status === 'error') { error.value = j.error; break }
      }
    }
    emit('done')
    if (!warnings.value.length) emit('close')
  } catch (e) {
    error.value = '整合失敗：' + errText(e)
  } finally { running.value = false; progress.value = null }
}

onMounted(buildPlan)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="merge" :size="16" /> 整合資料夾 — {{ char.name }}</span>
        <button class="btn ghost" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div class="body">
        <label class="lab">整合到哪個位置（其他位置的檔案會搬過來）</label>
        <select v-model="dstPath" :disabled="running" @change="buildPlan">
          <option v-for="p in char.paths" :key="p" :value="p">{{ p }}</option>
        </select>

        <div v-if="loading" class="msg">分析中…</div>
        <div v-else-if="error" class="msg err">{{ error }}</div>
        <template v-else-if="summary">
          <div class="cards">
            <div class="card"><b>{{ summary.move }}</b><span>直接移動</span></div>
            <div class="card"><b>{{ summary.rename }}</b><span>重命名後移動</span></div>
            <div class="card danger"><b>{{ summary.delete }}</b><span>刪除重複</span></div>
          </div>
          <div class="note">共搬移 {{ fmtSize(summary.bytes) }}；來源資料夾清空後會自動移除</div>

          <div v-if="summary.rename" class="renames">
            <div class="rlab">重命名清單</div>
            <template v-for="g in plan" :key="g.src">
              <div v-for="p in g.plan.filter(x => x.action === 'rename')" :key="p.src" class="rrow">
                {{ p.name }} → {{ p.dst.split('\\').pop() }}
              </div>
            </template>
          </div>
        </template>

        <div v-if="capacity" class="cap" :class="{ bad: !capacity.fits }">
          <template v-if="!capacity.crossVolume">
            <Icon name="check" :size="13" /> 同一磁碟，直接改名即可完成，不需額外空間
          </template>
          <template v-else-if="capacity.free < 0">
            <Icon name="warn" :size="13" /> 跨磁碟搬移 {{ fmtSize(capacity.need) }}，無法讀取目標剩餘空間
          </template>
          <template v-else-if="capacity.fits">
            <Icon name="check" :size="13" /> 跨磁碟搬移 {{ fmtSize(capacity.need) }}，
            目標剩餘 {{ fmtSize(capacity.free) }}
          </template>
          <template v-else>
            <Icon name="warn" :size="13" /> 空間不足：需要 {{ fmtSize(capacity.need) }}，
            目標僅剩 {{ fmtSize(capacity.free) }}（差 {{ fmtSize(capacity.need - capacity.free) }}）
          </template>
        </div>

        <div v-if="warnings.length" class="warns">
          <div class="wh ib"><Icon name="warn" :size="14" /> 整合完成，但有需要你處理的項目：</div>
          <div v-for="(w, i) in warnings" :key="i" class="wrow">{{ w }}</div>
        </div>

        <div v-if="progress" class="prog">
          <div class="ptxt">
            {{ progress.phase === 'delete' ? '刪除' : '移動' }}
            {{ progress.index ? `(${progress.index}/${progress.total})` : '' }}
            {{ progress.name }}
          </div>
          <div class="bar"><div class="fill"
            :style="{ width: (progress.size ? progress.done / progress.size * 100 : 0) + '%' }"></div></div>
        </div>
      </div>

      <div class="foot">
        <!-- Say *why* it cannot be pressed. A greyed-out button next to a small
             "分析中…" line reads as a dead button, not as "please wait". -->
        <button class="btn ib" :disabled="!summary || running || loading || capacity?.fits === false" @click="run">
          <Icon v-if="loading || running" name="spinner" :size="13" class="spin" />
          {{ loading ? '分析中，請稍候…' : running ? '整合中…' : '確認整合' }}
        </button>
        <button class="btn ghost" :disabled="running" @click="emit('close')">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.warns { margin-top: 10px; border: 1px solid #f9e2af; border-radius: 6px;
  padding: 8px 10px; font-size: 12px; }
.wh { color: #f9e2af; margin-bottom: 4px; }
.wrow { color: var(--dim); font-size: 11px; }
.cap { display: flex; align-items: center; gap: 6px; font-size: 12px;
  color: var(--success); margin-top: 10px; }
.cap.bad { color: var(--danger); }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.8);
  display: flex; align-items: center; justify-content: center; z-index: 60; }
.dlg { background: var(--panel); border-radius: 10px; width: min(620px, 94vw);
  max-height: 88vh; max-height: 88dvh; display: flex; flex-direction: column; }
.dhead { display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid #313148; font-weight: bold; }
.body { padding: 14px 16px; overflow-y: auto; }
.lab { display: block; font-size: 12px; font-weight: bold; margin-bottom: 6px; }
select { width: 100%; background: var(--card); color: var(--text);
  border: 1px solid #555; border-radius: 5px; padding: 7px; font-size: 12px; }
.cards { display: flex; gap: 10px; margin-top: 14px; }
.card { flex: 1; background: var(--card); border-radius: 8px; padding: 12px;
  text-align: center; }
.card b { display: block; font-size: 20px; color: var(--success); }
.card span { font-size: 11px; color: var(--dim); }
.card.danger b { color: var(--danger); }
.note { color: var(--dim); font-size: 11px; margin-top: 10px; }
.renames { margin-top: 12px; }
.rlab { font-size: 11px; color: var(--dim); margin-bottom: 4px; }
.rrow { font-size: 10px; color: var(--dim); padding: 2px 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.prog { margin-top: 14px; }
.ptxt { font-size: 11px; color: var(--dim); margin-bottom: 4px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar { height: 6px; background: var(--card); border-radius: 3px; overflow: hidden; }
.fill { height: 100%; background: var(--accent); transition: width .2s; }
.msg { padding: 20px; text-align: center; color: var(--dim); }
.msg.err { color: var(--danger); }
.foot { display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 16px; border-top: 1px solid #313148; }
</style>
