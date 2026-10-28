<script setup>
import { ref, onMounted } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const emit = defineEmits(['close'])

const rows = ref([])
const loading = ref(true)
const busy = ref('')
const error = ref('')

async function load() {
  loading.value = true
  try {
    rows.value = (await api('/api/auth/sessions')).sessions || []
  } catch (e) { error.value = errText(e) }
  finally { loading.value = false }
}

async function revoke(r) {
  if (!confirm(`把「${r.device}（${r.ip}）」登出？`)) return
  busy.value = r.id
  error.value = ''
  try {
    await api(`/api/auth/sessions/${r.id}`, { method: 'DELETE' })
    await load()
  } catch (e) { error.value = errText(e) }
  finally { busy.value = '' }
}

function when(iso) {
  const d = new Date(iso)
  if (isNaN(d)) return iso
  const mins = Math.round((Date.now() - d) / 60000)
  if (mins < 2) return '剛剛'
  if (mins < 60) return `${mins} 分鐘前`
  if (mins < 60 * 24) return `${Math.round(mins / 60)} 小時前`
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

onMounted(load)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="gauge" :size="15" /> 登入中的裝置</span>
        <span class="spacer"></span>
        <button class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 關閉
        </button>
      </div>

      <p class="intro">
        這個帳號目前有效的登入。手機弄丟時把那一筆登出就好，
        不必改密碼，其他裝置也不會受影響。
      </p>

      <div v-if="loading" class="dim">讀取中…</div>
      <div v-else-if="!rows.length" class="dim">沒有其他登入。</div>
      <div v-else class="list">
        <div v-for="r in rows" :key="r.id" class="row" :class="{ me: r.current }">
          <div class="who">
            <b>{{ r.device }}</b>
            <span v-if="r.current" class="tag">這台</span>
            <div class="sub">{{ r.ip }} · 最後使用 {{ when(r.last_seen) }}</div>
          </div>
          <button v-if="!r.current" class="btn ghost small"
                  :disabled="busy === r.id" @click="revoke(r)">登出</button>
        </div>
      </div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="note">
        登入有效期是 7 天，過期會自動消失。改密碼則會一次登出所有裝置。
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border-radius: 10px; padding: 14px 16px;
  width: min(460px, 94vw); max-height: 90vh; max-height: 90dvh; overflow-y: auto;
  display: flex; flex-direction: column; gap: 8px;
  color: var(--text); font-size: 13px; }
.dhead { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.spacer { flex: 1; }
.intro { margin: 0; color: var(--dim); font-size: 12px; line-height: 1.6; }
.dim { color: var(--dim); font-size: 12px; }
.list { display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; align-items: center; gap: 10px; background: var(--card);
  border-radius: 6px; padding: 8px 10px; }
.row.me { border: 1px solid var(--accent); }
.who { flex: 1; min-width: 0; }
.sub { color: var(--dim); font-size: 11px; margin-top: 2px; }
.tag { background: var(--accent); color: #fff; border-radius: 8px;
  padding: 0 6px; font-size: 10px; margin-left: 6px; }
.btn.small { font-size: 11px; padding: 3px 10px; }
.err { color: var(--danger, #e66); font-size: 12px; }
.note { color: var(--dim); font-size: 11px; line-height: 1.6; }
</style>
