<script setup>
/**
 * Jobs a restart of the service cut short, waiting for their owner.
 *
 * Scans come back by themselves; what lands here is what writes files
 * (轉檔、壓縮、歸檔、整合、剪輯) — never continued without asking, because the
 * restart may have been how somebody stopped it — and a scan that was
 * interrupted again and again. The server has already removed whatever was
 * half written, so continuing is safe and skips what was finished.
 */
import { ref } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const props = defineProps({ jobs: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'resumed'])

const rows = ref([...props.jobs])
const busy = ref('')
const error = ref('')

function what(j) {
  if (j.left === -1) return '掃描：會再跑一次，做過的部分有快取，很快就會追上'
  if (j.left === 0) return j.total ? `${j.total} 個都已經處理完了` : '已經完成了'
  if (j.left == null) return '還沒完成：繼續會從頭再做一次（半寫的檔案已清掉）'
  const fail = j.failed ? `（其中 ${j.failed} 個失敗）` : ''
  return `完成 ${j.finished}／${j.total}${fail}，剩 ${j.left} 個`
}

function when(sec) {
  if (!sec) return ''
  const d = new Date(sec * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function drop(j) {
  rows.value = rows.value.filter(r => r.id !== j.id)
  if (!rows.value.length) emit('close')
}

async function resume(j) {
  busy.value = j.id
  error.value = ''
  try {
    const r = await api(`/api/jobs/interrupted/${j.id}/resume`, { method: 'POST' })
    drop(j)
    if (r.job_id) emit('resumed', r)
  } catch (e) { error.value = `${j.label}：${errText(e)}` }
  finally { busy.value = '' }
}

async function dismiss(j) {
  busy.value = j.id
  error.value = ''
  try {
    await api(`/api/jobs/interrupted/${j.id}/dismiss`, { method: 'POST' })
    drop(j)
  } catch (e) { error.value = errText(e) }
  finally { busy.value = '' }
}
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="warn" :size="15" /> 上次重啟時中斷的工作</span>
        <span class="spacer"></span>
        <button class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 稍後再說
        </button>
      </div>

      <p class="intro">
        服務重新啟動時，這些工作還在進行。會寫入檔案的工作不會自己繼續——
        你可能就是為了停下它才重啟的。寫到一半的檔案已經清掉了。
      </p>

      <div class="list">
        <div v-for="j in rows" :key="j.id" class="row">
          <div class="who">
            <b>{{ j.label }}</b>
            <div class="sub">{{ what(j) }}</div>
            <div v-if="j.reason" class="sub warn">{{ j.reason }}</div>
            <div class="sub">開始於 {{ when(j.started_at) }}</div>
          </div>
          <button v-if="j.left !== 0" class="btn small"
                  :disabled="!!busy" @click="resume(j)">繼續</button>
          <button class="btn ghost small" :disabled="!!busy" @click="dismiss(j)">
            {{ j.left === 0 ? '知道了' : '略過' }}</button>
        </div>
      </div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="note">「稍後再說」會留著這份清單，下次開啟網頁時再問一次。</div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border-radius: 10px; padding: 14px 16px;
  width: min(520px, 94vw); max-height: 90vh; max-height: 90dvh; overflow-y: auto;
  display: flex; flex-direction: column; gap: 8px;
  color: var(--text); font-size: 13px; }
.dhead { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.spacer { flex: 1; }
.intro { margin: 0; color: var(--dim); font-size: 12px; line-height: 1.6; }
.list { display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; align-items: center; gap: 8px; background: var(--card);
  border-radius: 6px; padding: 8px 10px; }
.who { flex: 1; min-width: 0; }
.who b { word-break: break-all; }
.sub { color: var(--dim); font-size: 11px; margin-top: 2px; }
.sub.warn { color: var(--warn, #e0b050); }
.btn.small { font-size: 11px; padding: 3px 10px; flex: 0 0 auto; }
.err { color: var(--danger, #e66); font-size: 12px; }
.note { color: var(--dim); font-size: 11px; line-height: 1.6; }
</style>
