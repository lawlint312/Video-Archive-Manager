<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { store } from '../store'
import Icon from './Icon.vue'
import { POLICY, passwordProblem } from '../password'

const users = ref([])
const roots = ref([])        // every configured root (archive + inbox)
const inbox = ref([])        // which of those are inbox folders
const loading = ref(true)
const error = ref('')
const busy = ref(false)

// the row being edited, or 'new'
const editing = ref(null)
const form = ref(null)

// Permission groups come from the server now (設定 → 權限組), so a group an
// admin just created is choosable here without a code change.
const roleList = ref([])      // [{ key, name, perms, fixed }]
const catalogue = ref([])
function roleLabel(r) { return (roleList.value.find(x => x.key === r) || {}).name || r }
function roleDesc(r) {
  if (r.fixed) return '全部功能＋設定與帳號管理'
  if (!r.perms.length) return '只能瀏覽與播放'
  const label = k => (catalogue.value.find(p => p.key === k) || {}).label || k
  return r.perms.map(label).join('、')
}

async function load() {
  loading.value = true
  try {
    const [d, rl] = await Promise.all([api('/api/users'), api('/api/roles')])
    roleList.value = rl.roles
    catalogue.value = rl.catalogue
    users.value = d.users
    roots.value = d.roots || []
    inbox.value = d.inbox || []
  } catch (e) {
    error.value = '載入失敗：' + errText(e)
  } finally { loading.value = false }
}

function isInbox(p) { return inbox.value.includes(p) }

function startNew() {
  error.value = ''
  editing.value = 'new'
  form.value = { username: '', password: '', role: 'viewer',
                 scoped: false, picked: [], ips: '' }
}

function startEdit(u) {
  error.value = ''
  editing.value = u.id
  form.value = { username: u.username, password: '', role: u.role,
                 scoped: (u.allowed_roots || []).length > 0,
                 picked: [...(u.allowed_roots || [])],
                 ips: (u.allowed_ips || []).join('\n') }
}

function cancel() { editing.value = null; form.value = null; error.value = '' }

function togglePick(p) {
  const i = form.value.picked.indexOf(p)
  i >= 0 ? form.value.picked.splice(i, 1) : form.value.picked.push(p)
}

const scopeOk = computed(() =>
  !form.value?.scoped || form.value.picked.length > 0)

// One per line, blank lines ignored. Checked again on the server — this copy
// only exists so the message appears before a round trip.
const ipList = () => (form.value?.ips || '')
  .split(/[\n,]/).map(s => s.trim()).filter(Boolean)


async function save() {
  const f = form.value
  if (!scopeOk.value) { error.value = '限制範圍時至少要勾選一個資料夾'; return }
  busy.value = true
  error.value = ''
  try {
    if (editing.value === 'new') {
      if (!f.username.trim()) throw { detail: '請輸入帳號' }
      const problem = passwordProblem(f.password)
      if (problem) throw { detail: problem }
      await api('/api/users', {
        method: 'POST',
        body: JSON.stringify({
          username: f.username.trim(), password: f.password, role: f.role,
          allowed_roots: f.scoped ? f.picked : [],
          allowed_ips: ipList(),
        }),
      })
    } else {
      const body = { role: f.role, set_roots: true,
                     allowed_roots: f.scoped ? f.picked : [],
                     set_ips: true, allowed_ips: ipList() }
      if (f.password) {
        const problem = passwordProblem(f.password)
        if (problem) throw { detail: problem }
        body.password = f.password
      }
      await api(`/api/users/${editing.value}`, {
        method: 'PATCH', body: JSON.stringify(body),
      })
    }
    cancel()
    await load()
  } catch (e) {
    error.value = e.detail || ('操作失敗：' + (e.status || e))
  } finally { busy.value = false }
}

async function setDisabled(u, disabled) {
  const what = disabled ? '停用' : '啟用'
  if (disabled && !confirm(`確定停用帳號「${u.username}」？
`
      + '帳號與其標記都會保留，但無法再登入，目前的登入也會立刻失效。')) return
  busy.value = true
  error.value = ''
  try {
    await api(`/api/users/${u.id}`, { method: 'PATCH', body: JSON.stringify({ disabled }) })
    await load()
  } catch (e) {
    error.value = e.detail || (what + '失敗：' + (e.status || e))
  } finally { busy.value = false }
}

async function reset2fa(u) {
  if (!confirm(`重設「${u.username}」的兩步驟驗證？
`
      + '用在對方手機遺失、備用碼也用完的時候。重設後只要知道密碼就能登入，'
      + '有編輯權限的帳號下次登入時必須重新設定。')) return
  busy.value = true
  error.value = ''
  try {
    await api(`/api/users/${u.id}`, { method: 'PATCH',
      body: JSON.stringify({ reset_2fa: true }) })
    await load()
  } catch (e) {
    error.value = e.detail || ('重設失敗：' + (e.status || e))
  } finally { busy.value = false }
}

async function remove(u) {
  if (!confirm(`確定刪除帳號「${u.username}」？\n該帳號目前的登入狀態也會一併失效。`)) return
  busy.value = true
  error.value = ''
  try {
    await api(`/api/users/${u.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.detail || ('刪除失敗：' + (e.status || e))
  } finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <div class="wrap">
    <div v-if="loading" class="msg">載入中…</div>

    <template v-else>
      <table class="ulist">
        <thead>
          <tr><th>帳號</th><th>權限</th><th>可存取範圍</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id"
              :class="{ me: u.id === store.user?.id, off: u.disabled }">
            <td class="un">{{ u.username }}
              <span v-if="u.id === store.user?.id" class="tag">你</span>
              <span v-if="u.disabled" class="tag offtag">已停用</span></td>
            <td>{{ roleLabel(u.role) }}
              <span v-if="u.twofa" class="tag twofa" title="已啟用兩步驟驗證">2FA</span></td>
            <td class="scope">
              <span v-if="!u.allowed_roots.length" class="all">全部</span>
              <span v-else :title="u.allowed_roots.join('\n')">
                {{ u.allowed_roots.length }} 個資料夾</span>
            </td>
            <td class="ops">
              <button class="lnk" @click="startEdit(u)">編輯</button>
              <!-- Between 編輯 and 刪除 because that is what it is: a way to
                   shut an account down without throwing away its marks and
                   history, and without freeing the name up again. -->
              <button v-if="u.twofa" class="lnk"
                      title="手機遺失時用：清掉兩步驟驗證，讓對方用密碼登入後重設"
                      @click="reset2fa(u)">重設 2FA</button>
              <button class="lnk" :disabled="u.id === store.user?.id"
                      :title="u.disabled ? '讓這個帳號可以再次登入'
                                         : '保留帳號與資料，但不能再登入'"
                      @click="setDisabled(u, !u.disabled)">
                {{ u.disabled ? '啟用' : '停用' }}</button>
              <button class="lnk del" :disabled="u.id === store.user?.id"
                      @click="remove(u)">刪除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <button v-if="!editing" class="btn addbtn ib" @click="startNew">
        <Icon name="plus" :size="14" /> 新增帳號</button>

      <!-- create / edit form -->
      <div v-if="editing" class="form">
        <div class="fhead">{{ editing === 'new' ? '新增帳號' : `編輯「${form.username}」` }}</div>

        <label class="lab">帳號</label>
        <!-- autocomplete off + new-password below: this form sets credentials
             for SOMEONE ELSE, but a text field followed by a password field
             reads to the browser as a login form. Once the admin's own login
             was saved (the login page became a real <form> on 2026-09-15),
             Chrome started filling the admin's username and password in here.
             In edit mode that is not a nuisance but a hazard: saving another
             account's role would silently set its password to the admin's. -->
        <input v-model="form.username" :disabled="editing !== 'new'"
               name="vam-account-name" autocomplete="off"
               autocapitalize="off" spellcheck="false"
               placeholder="使用者名稱" />

        <label class="lab">{{ editing === 'new' ? '密碼' : '新密碼（留空表示不變更）' }}</label>
        <input v-model="form.password" type="password"
               name="vam-account-new-password" autocomplete="new-password"
               placeholder="8 字元以上，含大小寫英文與數字" />
        <div class="hint">{{ POLICY }}</div>

        <label class="lab">權限</label>
        <div class="roles">
          <label v-for="r in roleList" :key="r.key" class="role"
                 :class="{ on: form.role === r.key }">
            <input type="radio" :value="r.key" v-model="form.role" />
            <b>{{ r.name }}</b><span>{{ roleDesc(r) }}</span>
          </label>
          <div class="hint">要調整各組能用哪些功能，請到「權限組」分頁。</div>
        </div>

        <label class="lab">允許登入的來源 IP</label>
        <textarea v-model="form.ips" rows="3" class="ipbox"
                  placeholder="留空 = 不限制&#10;192.168.1.0/24&#10;203.0.113.5"></textarea>
        <div class="hint">
          一行一筆，可用單一位址或網段（CIDR）。<b>留空表示不限制。</b>
          每次請求都會檢查，不只登入時。
          <br>提醒：家用網路的 IP 會因 DHCP 變動，而且同網段的人有辦法偽造來源位址
          —— 這一項擋得住「別台裝置誤用這個帳號」，擋不住有心人。
          限制自己的帳號時，系統會拒絕一份不含你目前位址的清單。
        </div>

        <label class="lab">可存取的資料夾</label>
        <label class="chk">
          <input type="checkbox" v-model="form.scoped" />
          限制此帳號只能存取部分資料夾
        </label>
        <div v-if="!form.scoped" class="hint">
          未限制 — 可存取設定中所有的歸檔根目錄與待分類資料夾。
        </div>
        <div v-else class="rootbox">
          <label v-for="r in roots" :key="r" class="rootrow">
            <input type="checkbox" :checked="form.picked.includes(r)"
                   @change="togglePick(r)" />
            <span class="kind" :class="{ ib: true }">
              <Icon :name="isInbox(r) ? 'inbox' : 'folder'" :size="12" />
              {{ isInbox(r) ? '待分類' : '歸檔' }}</span>
            <span class="rp">{{ r }}</span>
          </label>
          <div v-if="!roots.length" class="hint">（設定裡還沒有任何資料夾）</div>
        </div>

        <div v-if="error" class="err">{{ error }}</div>

        <div class="frow">
          <button class="btn" :disabled="busy" @click="save">
            {{ busy ? '儲存中…' : '儲存' }}</button>
          <button class="btn ghost" :disabled="busy" @click="cancel">取消</button>
          <span v-if="editing !== 'new'" class="note">
            變更權限或密碼會讓該帳號目前的登入失效，需要重新登入。</span>
        </div>
      </div>

      <div v-if="error && !editing" class="err">{{ error }}</div>
    </template>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 10px; }
.msg { padding: 30px; text-align: center; color: var(--dim); }

.ulist { width: 100%; border-collapse: collapse; font-size: 12px; }
.ulist th { text-align: left; color: var(--dim); font-size: 11px;
  font-weight: normal; padding: 4px 8px; border-bottom: 1px solid #313148; }
.ulist td { padding: 6px 8px; border-bottom: 1px solid #26263a; }
.ulist tr.me .un { color: var(--accent); }
.ulist tr.off td { opacity: .55; }
.tag.offtag { background: #5a2020; }
.tag.twofa { background: #2a4a35; color: #9fe0b5; }
.un { font-weight: bold; }
.tag { background: var(--accent); color: #fff; border-radius: 8px;
  padding: 0 6px; font-size: 10px; margin-left: 6px; font-weight: normal; }
.scope .all { color: var(--dim); }
.ops { text-align: right; white-space: nowrap; }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; padding: 0 4px; }
.lnk:hover { text-decoration: underline; }
.lnk.del { color: var(--danger); }
.lnk:disabled { color: #555; cursor: default; text-decoration: none; }

.addbtn { align-self: flex-start; }
.form { border: 1px solid #404058; border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; gap: 6px; }
.fhead { font-weight: bold; font-size: 13px; margin-bottom: 4px; }
.lab { font-size: 12px; font-weight: bold; margin-top: 8px; }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.ipbox { width: 100%; font-family: inherit; font-size: 12px;
  background: #1b1b2a; border: 1px solid #313148; border-radius: 6px;
  color: var(--text); padding: 6px 8px; resize: vertical; }
.hint { color: var(--dim); font-size: 11px; }

.roles { display: flex; flex-direction: column; gap: 4px; }
.role { display: flex; align-items: baseline; gap: 8px; font-size: 12px;
  padding: 4px 6px; border-radius: 4px; cursor: pointer; }
.role.on { background: #26263a; }
.role span { color: var(--dim); font-size: 11px; }
.role input { width: auto; margin: 0; flex: 0 0 auto; }

.rootbox { border: 1px solid #313148; border-radius: 6px; padding: 6px 8px;
  max-height: 170px; overflow-y: auto; }
.rootrow { display: flex; align-items: center; gap: 8px; font-size: 12px;
  line-height: 20px; cursor: pointer; }
.kind { color: var(--dim); font-size: 10px; flex: 0 0 56px; }
.rp { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.frow { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.note { color: var(--dim); font-size: 10px; }
.err { color: var(--danger); font-size: 12px; }
</style>
