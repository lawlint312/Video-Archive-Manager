<script setup>
import { ref, onMounted } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'

const props = defineProps({
  // Set when the account's role requires two-factor and it has none yet: the
  // server refuses everything else until this is done, so there is no way out
  // of the dialog except finishing it or signing out.
  forced: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'enabled', 'logout'])

const state = ref('loading')   // loading | status | setup | codes
const info = ref(null)         // { enabled, required, recovery_left }
const password = ref('')
const code = ref('')
const secret = ref('')
const svg = ref('')
const codes = ref([])          // recovery codes, shown exactly once
const saved = ref(false)       // "I have written them down"
const error = ref('')
const busy = ref(false)

async function load() {
  try {
    info.value = await api('/api/auth/2fa')
    state.value = 'status'
  } catch (e) { error.value = errText(e) }
}

async function startSetup() {
  busy.value = true; error.value = ''
  try {
    const r = await api('/api/auth/2fa/setup', { method: 'POST',
      body: JSON.stringify({ password: password.value }) })
    secret.value = r.secret
    svg.value = r.svg || ''
    code.value = ''
    state.value = 'setup'
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

async function enable() {
  busy.value = true; error.value = ''
  try {
    const r = await api('/api/auth/2fa/enable', { method: 'POST',
      body: JSON.stringify({ code: code.value }) })
    codes.value = r.recovery_codes || []
    state.value = 'codes'
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

async function disable() {
  busy.value = true; error.value = ''
  try {
    await api('/api/auth/2fa/disable', { method: 'POST',
      body: JSON.stringify({ password: password.value, code: code.value }) })
    password.value = ''; code.value = ''
    await load()
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

async function newCodes() {
  busy.value = true; error.value = ''
  try {
    const r = await api('/api/auth/2fa/recovery', { method: 'POST',
      body: JSON.stringify({ password: password.value }) })
    codes.value = r.recovery_codes || []
    saved.value = false
    state.value = 'codes'
  } catch (e) { error.value = errText(e) }
  finally { busy.value = false }
}

function copyCodes() {
  try { navigator.clipboard?.writeText(codes.value.join('\n')) } catch { /* ignore */ }
}

// Only now is the account really covered, so this is what App listens for.
function finish() { emit('enabled'); emit('close') }

onMounted(load)
</script>

<template>
  <div class="modal" @click.self="props.forced || emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="check" :size="15" /> 兩步驟驗證</span>
        <span class="spacer"></span>
        <button v-if="!props.forced" class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 關閉
        </button>
      </div>

      <div v-if="props.forced && state !== 'codes'" class="forced">
        這個帳號有編輯權限（所屬權限組勾了至少一項功能），所以必須開啟兩步驟驗證才能繼續使用。
      </div>

      <div v-if="state === 'loading'" class="dim">讀取中…</div>

      <!-- current state -->
      <template v-else-if="state === 'status'">
        <p class="intro">
          登入時除了密碼，還要輸入驗證 App 上的 6 位數字。
          這樣就算密碼外洩，別人也登不進來。
        </p>
        <div class="row">
          狀態：<b :class="info.enabled ? 'on' : 'off'">
            {{ info.enabled ? '已啟用' : '尚未啟用' }}</b>
          <span v-if="info.enabled" class="dim">· 備用碼還剩 {{ info.recovery_left }} 組</span>
        </div>

        <template v-if="!info.enabled">
          <label class="lab">輸入目前的密碼開始設定</label>
          <input v-model="password" type="password" autocomplete="current-password"
                 name="vam-2fa-password" @keyup.enter="startSetup" />
          <div class="foot">
            <span class="spacer"></span>
            <button v-if="props.forced" class="btn ghost" @click="emit('logout')">登出</button>
            <button class="btn" :disabled="busy || !password" @click="startSetup">
              開始設定</button>
          </div>
        </template>

        <template v-else>
          <div class="note">
            關掉或重新產生備用碼都要再輸入一次密碼與驗證碼。
            <template v-if="info.required">
              這個帳號的權限<b>必須</b>保持啟用，只能重新產生備用碼。
            </template>
          </div>
          <label class="lab">目前的密碼</label>
          <input v-model="password" type="password" autocomplete="current-password"
                 name="vam-2fa-password" />
          <label class="lab">驗證 App 上的 6 位數字</label>
          <input v-model="code" inputmode="numeric" autocomplete="one-time-code"
                 maxlength="6" placeholder="000000" />
          <div class="foot">
            <button class="btn ghost" :disabled="busy || !password" @click="newCodes">
              重新產生備用碼</button>
            <span class="spacer"></span>
            <button v-if="!info.required" class="btn ghost danger"
                    :disabled="busy || !password || !code" @click="disable">
              關閉兩步驟驗證</button>
          </div>
        </template>
      </template>

      <!-- scan the QR -->
      <template v-else-if="state === 'setup'">
        <p class="intro">
          用 Google Authenticator、Microsoft Authenticator 或 1Password 掃描這個 QR code，
          再輸入它顯示的 6 位數字。
        </p>
        <div class="qr" v-if="svg" v-html="svg"></div>
        <div class="manual">
          掃不到的話，在 App 裡選「手動輸入設定金鑰」並輸入：
          <code>{{ secret }}</code>
        </div>
        <label class="lab">App 顯示的 6 位數字</label>
        <input v-model="code" inputmode="numeric" autocomplete="one-time-code"
               maxlength="6" placeholder="000000" @keyup.enter="enable" />
        <div v-if="error" class="err">{{ error }}</div>
        <div class="foot">
          <button class="btn ghost" :disabled="busy" @click="state = 'status'">上一步</button>
          <span class="spacer"></span>
          <button class="btn" :disabled="busy || code.length < 6" @click="enable">
            啟用</button>
        </div>
      </template>

      <!-- recovery codes, shown exactly once -->
      <template v-else-if="state === 'codes'">
        <div class="forced">
          <b>請把這 10 組備用碼抄下來或存進密碼管理員。</b>
          手機遺失時，它們是你唯一的入口，而且<b>這一頁關掉就再也看不到</b>。
          每組只能用一次。
        </div>
        <div class="codes">
          <code v-for="c in codes" :key="c">{{ c }}</code>
        </div>
        <label class="chk">
          <input type="checkbox" v-model="saved" /> 我已經把備用碼保存好了
        </label>
        <div class="foot">
          <button class="btn ghost" @click="copyCodes">複製</button>
          <span class="spacer"></span>
          <button class="btn" :disabled="!saved" @click="finish">完成</button>
        </div>
      </template>

      <div v-if="error && state !== 'setup'" class="err">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border-radius: 10px; padding: 14px 16px;
  width: min(460px, 94vw); max-height: 92vh; max-height: 92dvh; overflow-y: auto;
  display: flex; flex-direction: column; gap: 8px;
  color: var(--text); font-size: 13px; }
.dhead { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.spacer { flex: 1; }
.intro { margin: 0; color: var(--dim); line-height: 1.6; font-size: 12px; }
.row { display: flex; align-items: center; gap: 8px; }
.row .on { color: #6cc28a; }
.row .off { color: #e9a24a; }
.lab { color: var(--dim); font-size: 12px; margin-top: 6px; }
.note { color: var(--dim); font-size: 11px; line-height: 1.6; }
.dim { color: var(--dim); font-size: 12px; }
.err { color: var(--danger, #e66); font-size: 12px; }
.forced { border: 1px solid #f38ba844; background: #f38ba814; color: #f38ba8;
  border-radius: 6px; padding: 8px 10px; font-size: 12px; line-height: 1.7; }
.qr { display: flex; justify-content: center; padding: 6px; }
.qr :deep(svg) { width: min(220px, 60vw); height: auto; }
.manual { color: var(--dim); font-size: 11px; line-height: 1.7; }
.manual code { color: var(--text); background: var(--card); border-radius: 4px;
  padding: 2px 6px; user-select: all; word-break: break-all; }
.codes { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px;
  background: var(--card); border-radius: 6px; padding: 10px; }
.codes code { font-size: 13px; letter-spacing: .5px; user-select: all; }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.btn.danger { color: var(--danger); border-color: #5a2020; }
</style>
