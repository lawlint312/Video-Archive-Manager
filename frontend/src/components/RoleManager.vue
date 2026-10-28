<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const roles = ref([])
const catalogue = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const editing = ref(null)     // role key, or 'new'
const form = ref(null)        // { name, perms: [] }

// The catalogue grouped by section, in the server's order — so a new
// permission added in roles.py shows up here without touching this file.
const sections = computed(() => {
  const out = []
  for (const p of catalogue.value) {
    let s = out.find(x => x.name === p.section)
    if (!s) { s = { name: p.section, perms: [] }; out.push(s) }
    s.perms.push(p)
  }
  return out
})
const labelOf = k => (catalogue.value.find(p => p.key === k) || {}).label || k

async function load() {
  loading.value = true
  try {
    const d = await api('/api/roles')
    roles.value = d.roles
    catalogue.value = d.catalogue
  } catch (e) { error.value = '載入失敗：' + errText(e) }
  finally { loading.value = false }
}

function startNew() {
  error.value = ''
  editing.value = 'new'
  form.value = { name: '', perms: [] }
}
function startEdit(r) {
  error.value = ''
  editing.value = r.key
  form.value = { name: r.name, perms: [...r.perms] }
}
function cancel() { editing.value = null; form.value = null; error.value = '' }

function toggle(k) {
  const i = form.value.perms.indexOf(k)
  i >= 0 ? form.value.perms.splice(i, 1) : form.value.perms.push(k)
}
function setAll(on) { form.value.perms = on ? catalogue.value.map(p => p.key) : [] }

async function save() {
  busy.value = true
  error.value = ''
  try {
    const body = JSON.stringify({ name: form.value.name.trim(), perms: form.value.perms })
    if (editing.value === 'new') await api('/api/roles', { method: 'POST', body })
    else await api(`/api/roles/${editing.value}`, { method: 'PATCH', body })
    cancel()
    await load()
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

async function remove(r) {
  if (!confirm(`刪除權限組「${r.name}」？`)) return
  busy.value = true
  error.value = ''
  try {
    await api(`/api/roles/${r.key}`, { method: 'DELETE' })
    await load()
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <div class="wrap">
    <div class="intro">
      每個帳號屬於一個權限組，勾了哪些功能就只能用哪些。<b>管理員固定擁有全部權限</b>，
      另外只有管理員能改設定、管帳號、看登入紀錄與備份。
      <br>任何權限組只要勾了一項以上，組裡的帳號都<b>必須開啟兩步驟驗證</b>；什麼都沒勾的等同唯讀，可以自行選擇。
      修改勾選後，下一次操作就生效，不用重新登入。
    </div>

    <div v-if="loading" class="msg">載入中…</div>
    <template v-else>
      <div class="list">
        <div v-for="r in roles" :key="r.key" class="role" :class="{ fixed: r.fixed }">
          <div class="rhead">
            <b>{{ r.name }}</b>
            <span v-if="r.fixed" class="tag">固定</span>
            <span v-else-if="r.builtin" class="tag dim">內建</span>
            <span v-if="!r.fixed" class="members">{{ r.members }} 個帳號</span>
            <span v-if="r.fixed || r.perms.length" class="tag twofa">需兩步驟驗證</span>
            <span class="spacer"></span>
            <template v-if="!r.fixed">
              <button class="lnk" @click="startEdit(r)">編輯</button>
              <button v-if="!r.builtin" class="lnk del" :disabled="busy"
                      @click="remove(r)">刪除</button>
            </template>
          </div>
          <div class="perms">
            <span v-if="r.fixed" class="pchip all">全部功能 ＋ 設定、帳號管理、登入紀錄、備份</span>
            <span v-else-if="!r.perms.length" class="none">只能瀏覽（唯讀）</span>
            <span v-else v-for="k in r.perms" :key="k" class="pchip">{{ labelOf(k) }}</span>
          </div>
        </div>
      </div>

      <button v-if="!editing" class="btn addbtn ib" @click="startNew">
        <Icon name="plus" :size="14" /> 新增權限組</button>

      <div v-if="editing" class="form">
        <div class="fhead">{{ editing === 'new' ? '新增權限組' : `編輯「${form.name}」` }}</div>
        <label class="lab">名稱</label>
        <input v-model="form.name" maxlength="30" placeholder="例如：整理員"
               name="vam-role-name" autocomplete="off" />

        <div class="lab row">勾選可以使用的功能
          <span class="spacer"></span>
          <button class="lnk" @click="setAll(true)">全選</button>
          <button class="lnk" @click="setAll(false)">全不選</button>
        </div>
        <div v-for="s in sections" :key="s.name" class="section">
          <div class="sname">{{ s.name }}</div>
          <label v-for="p in s.perms" :key="p.key" class="perm"
                 :class="{ on: form.perms.includes(p.key) }">
            <input type="checkbox" :checked="form.perms.includes(p.key)"
                   @change="toggle(p.key)" />
            <b>{{ p.label }}</b><span>{{ p.hint }}</span>
          </label>
        </div>
        <div class="hint">
          <template v-if="form.perms.length">這個組的帳號必須開啟兩步驟驗證。</template>
          <template v-else>什麼都沒勾 = 唯讀，兩步驟驗證可自行選擇。</template>
        </div>

        <div v-if="error" class="err">{{ error }}</div>
        <div class="frow">
          <button class="btn" :disabled="busy || !form.name.trim()" @click="save">
            {{ busy ? '儲存中…' : '儲存' }}</button>
          <button class="btn ghost" :disabled="busy" @click="cancel">取消</button>
        </div>
      </div>

      <div v-if="error && !editing" class="err">{{ error }}</div>
    </template>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 10px; }
.intro { color: var(--dim); font-size: 12px; line-height: 1.7; }
.msg { padding: 30px; text-align: center; color: var(--dim); }
.list { display: flex; flex-direction: column; gap: 8px; }
.role { border: 1px solid #313148; border-radius: 8px; padding: 8px 10px; }
.role.fixed { border-color: #404058; background: #1b1b2a; }
.rhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.spacer { flex: 1; }
.members { color: var(--dim); font-size: 11px; }
.tag { background: var(--accent); color: #fff; border-radius: 8px; padding: 0 6px; font-size: 10px; }
.tag.dim { background: #404058; }
.tag.twofa { background: #2a4a35; color: #9fe0b5; }
.perms { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.pchip { background: var(--card); border-radius: 10px; padding: 1px 8px; font-size: 11px; }
.pchip.all { color: var(--accent); }
.none { color: var(--dim); font-size: 11px; }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; padding: 0 4px; }
.lnk:hover { text-decoration: underline; }
.lnk.del { color: var(--danger); }
.lnk:disabled { color: #555; cursor: default; text-decoration: none; }
.addbtn { align-self: flex-start; }
.form { border: 1px solid #404058; border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; gap: 6px; }
.fhead { font-weight: bold; font-size: 13px; }
.lab { font-size: 12px; font-weight: bold; margin-top: 8px; }
.lab.row { display: flex; align-items: center; gap: 6px; }
.section { margin-top: 4px; }
.sname { color: var(--dim); font-size: 11px; margin: 6px 0 4px; }
.perm { display: flex; align-items: center; gap: 8px; font-size: 12px;
  padding: 5px 8px; border-radius: 6px; cursor: pointer; }
.perm:hover { background: var(--hover); }
.perm.on { background: #2a2a44; }
.perm input { width: auto; margin: 0; flex: 0 0 auto; }
.perm span { color: var(--dim); font-size: 11px; }
.hint { color: var(--dim); font-size: 11px; margin-top: 4px; }
.err { color: var(--danger); font-size: 12px; }
.frow { display: flex; gap: 8px; margin-top: 8px; }
</style>
