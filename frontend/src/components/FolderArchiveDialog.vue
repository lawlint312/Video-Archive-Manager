<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { setStatus, setBusy } from '../store'
import Icon from './Icon.vue'
import { t } from '../i18n'
import { ts } from '../serverstrings.js'

const props = defineProps({ folder: { type: String, required: true } })
const emit = defineEmits(['close', 'done'])

const info = ref(null)
const roots = ref([])
const person = ref('')
const chosen = ref('')
const loading = ref(true)
const running = ref(false)
const error = ref('')

function fmt(b) {
  if (b < 0) return '?'
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(1) + ' GB'
  return (b / (1 << 20)).toFixed(0) + ' MB'
}

// a root can take the package if it has the folder's total size free
function fits(r) { return r.free < 0 || (info.value && r.free > info.value.bytes) }

// where a same-named character already lives (root path → true)
const existingRoots = computed(() => {
  const set = new Set()
  for (const e of info.value?.existing || []) {
    for (const p of e.paths) {
      const r = roots.value.find(x => p.toLowerCase().startsWith(x.path.toLowerCase()))
      if (r) set.add(r.path)
    }
  }
  return set
})

const chosenRoot = computed(() => roots.value.find(r => r.path === chosen.value))
const mergingInto = computed(() => existingRoots.value.has(chosen.value))
// existing folder found, but its drive can't take the package
const existingButFull = computed(() => {
  for (const p of existingRoots.value) {
    const r = roots.value.find(x => x.path === p)
    if (r && !fits(r)) return r
  }
  return null
})

async function load() {
  try {
    const [i, r] = await Promise.all([
      api(`/api/inbox/folder?folder=${encodeURIComponent(props.folder)}`),
      api('/api/archive/roots'),
    ])
    info.value = i
    roots.value = r.roots
    person.value = i.name

    // default: merge into the existing character's root when it has room,
    // otherwise the root with the most free space
    const okExisting = [...existingRoots.value]
      .map(p => roots.value.find(x => x.path === p))
      .filter(x => x && fits(x))
    chosen.value = okExisting.length
      ? okExisting[0].path
      : ([...roots.value].filter(fits).sort((a, b) => b.free - a.free)[0]?.path || '')
  } catch (e) {
    error.value = t('載入失敗：') + errText(e)
  } finally { loading.value = false }
}

async function run() {
  if (!chosen.value) { alert(t('請選擇歸檔位置')); return }
  const name = person.value.trim()
  if (!name) { alert(t('請輸入角色名稱')); return }
  if (!confirm(t('整包歸檔？\n\n{n} 個檔案（{size}）\n→ {dest}',
                 { n: info.value.files, size: fmt(info.value.bytes), dest: `${chosen.value}\\${name}` }))) return

  running.value = true
  try {
    const { job_id } = await api('/api/archive/folder', {
      method: 'POST',
      body: JSON.stringify({ folder: props.folder, person: name, root: chosen.value }),
    })
    emit('close')      // let the user keep working; progress goes to the status bar
    while (true) {
      await new Promise(r => setTimeout(r, 500))
      const j = await api(`/api/archive/job/${job_id}`)
      const p = j.progress
      if (p && typeof p === 'object') {
        const pct = p.size ? Math.round(p.done / p.size * 100) : 0
        setBusy(t('整包歸檔 {person} ({i}/{n}) {name} {pct}%', { person: name, i: p.index, n: p.count, name: p.name, pct }))
      }
      if (j.status === 'done') {
        setStatus(t('整包歸檔完成「{name}」：搬移 {moved}、略過重複 {skipped}',
                    { name, moved: j.result.moved, skipped: j.result.deleted }) +
                  (j.result.failed ? t('、失敗 {n}', { n: j.result.failed }) : ''), 'ok')
        emit('done', { person: name })
        break
      }
      if (j.status === 'error') { setStatus(t('整包歸檔失敗：') + ts(j.error), 'warn'); break }
    }
  } catch (e) {
    setStatus(t('整包歸檔失敗：') + errText(e), 'warn')
  } finally { running.value = false; setBusy('') }
}

onMounted(load)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="package" :size="16" /> {{ t('整包歸檔') }}</span>
        <button class="btn ghost" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div v-if="loading" class="msg">{{ t('分析中…') }}</div>
      <div v-else-if="error" class="msg err">{{ error }}</div>
      <div v-else class="body">
        <label class="lab">{{ t('角色名稱') }}</label>
        <input v-model="person" :placeholder="t('角色名稱')" />
        <div class="sub">
          {{ t('{n} 部影片', { n: info.videos }) }} · {{ t('共 {n} 個檔案', { n: info.files }) }} · {{ fmt(info.bytes) }}
          　{{ t('（子資料夾結構會保留）') }}
        </div>

        <label class="lab">{{ t('歸檔到哪個根目錄') }}</label>
        <div class="roots">
          <label v-for="r in roots" :key="r.path" class="root"
                 :class="{ bad: !fits(r), on: chosen === r.path }">
            <input type="radio" :value="r.path" v-model="chosen" :disabled="!fits(r)" />
            <span class="rpath">{{ r.path }}</span>
            <span class="rfree">{{ t('剩餘 {size}', { size: fmt(r.free) }) }}</span>
            <span v-if="existingRoots.has(r.path)" class="tag exist">{{ t('已有此角色') }}</span>
            <span v-if="!fits(r)" class="tag full">{{ t('空間不足') }}</span>
          </label>
        </div>

        <div v-if="mergingInto" class="hint ok">
          {{ t('將合併進已存在的「{name}」資料夾（同內容檔案會略過，不會覆蓋）', { name: person }) }}
        </div>
        <div v-else-if="existingButFull" class="hint warn">
          {{ t('「{name}」原本在 {path}，但該碟空間不足', { name: person, path: existingButFull.path }) }}<br />
          → {{ t('將建立在 {path}，之後側邊欄會顯示為跨碟合併群組，可用「整合資料夾」合併', { path: chosen }) }}
        </div>
      </div>

      <div class="foot">
        <button class="btn" :disabled="loading || running || !chosen" @click="run">
          {{ running ? t('歸檔中…') : t('開始歸檔') }}
        </button>
        <button class="btn ghost" @click="emit('close')">{{ t('取消') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.8);
  display: flex; align-items: center; justify-content: center; z-index: 60; }
.dlg { background: var(--panel); border-radius: 10px; width: min(620px, 94vw);
  max-height: 88vh; max-height: 88dvh; display: flex; flex-direction: column; }
.dhead { display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid #313148; font-weight: bold; }
.body { padding: 14px 16px; overflow-y: auto; }
.lab { display: block; font-size: 12px; font-weight: bold; margin: 14px 0 6px; }
.lab:first-child { margin-top: 0; }
input[type=text], .body > input { width: 100%; }
.sub { color: var(--dim); font-size: 11px; margin-top: 6px; }
.roots { display: flex; flex-direction: column; gap: 6px; }
.root { display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: var(--card); border: 1px solid #404058; border-radius: 6px;
  font-size: 12px; cursor: pointer; }
.root.on { border-color: var(--accent); }
.root.bad { opacity: .5; cursor: not-allowed; }
.rpath { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rfree { color: var(--dim); font-size: 11px; }
.tag { font-size: 10px; border-radius: 8px; padding: 1px 7px; }
.tag.exist { background: #1a3a1a; color: var(--success); }
.tag.full { background: #5a2020; color: var(--danger); }
.hint { margin-top: 12px; font-size: 11px; line-height: 1.7; padding: 8px 10px;
  border-radius: 6px; }
.hint.ok { background: #162416; color: var(--success); }
.hint.warn { background: #2a2010; color: #f9e2af; }
.msg { padding: 30px; text-align: center; color: var(--dim); }
.msg.err { color: var(--danger); }
.foot { display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 16px; border-top: 1px solid #313148; }
</style>
