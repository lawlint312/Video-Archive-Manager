<script setup>
import { ref } from 'vue'
import { api, errText } from '../api'
import { store } from '../store'
import Icon from './Icon.vue'

// Empty, not 'admin'. The prefill dated from the first commit, when this only
// ever ran on localhost. Once the login page was reachable from the internet
// (2026-09-15) it handed every visitor the administrator's username for free,
// leaving only the password to guess. The browser's own autofill still
// works for the real user through the autocomplete attributes below.
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
// Second step. The ticket is all the server gives back for an account with
// two-factor on — no cookie, so nothing is signed in until the code is right.
const ticket = ref('')
const code = ref('')
const recoveryLeft = ref(null)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    const r = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    if (r.need_2fa) {
      ticket.value = r.ticket
      code.value = ''
      return
    }
    store.user = r
    store.loginNotice = ''
  } catch (e) {
    error.value = e.status === 401 ? '帳號或密碼錯誤' : ('登入失敗：' + errText(e))
  } finally {
    busy.value = false
  }
}

async function submitCode() {
  error.value = ''
  busy.value = true
  try {
    const r = await api('/api/auth/login/2fa', {
      method: 'POST',
      body: JSON.stringify({ ticket: ticket.value, code: code.value }),
    })
    // Says it out loud: a recovery code is one of ten and does not come back.
    if (r.recovery_used) recoveryLeft.value = r.recovery_left
    store.loginNotice = r.recovery_used
      ? `已使用一組備用碼，還剩 ${r.recovery_left} 組。建議到「兩步驟驗證」重新產生。`
      : ''
    store.user = r
  } catch (e) {
    error.value = errText(e)
    // The ticket is gone after too many wrong codes — say so by going back.
    if (String(e.detail || '').includes('重新登入')) {
      ticket.value = ''
      password.value = ''
    }
  } finally {
    busy.value = false
  }
}

function backToPassword() { ticket.value = ''; code.value = ''; error.value = '' }
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <h1><img class="logo" src="/brand-lockup.png"
               alt="Video Archive Manager" /></h1>
      <p class="sub">請登入</p>
      <!-- A real <form> with name/autocomplete so password managers recognise
           the pair and offer to save and fill it. -->
      <form v-if="!ticket" class="login-form" @submit.prevent="submit">
        <input v-model="username" name="username" autocomplete="username"
               placeholder="帳號" autocapitalize="off" spellcheck="false" />
        <input v-model="password" name="password" type="password"
               autocomplete="current-password" placeholder="密碼" />
        <button class="btn" type="submit" :disabled="busy">
          {{ busy ? '登入中…' : '登入' }}
        </button>
      </form>

      <!-- second step -->
      <form v-else class="login-form" @submit.prevent="submitCode">
        <div class="step2">
          輸入驗證 App 上的 6 位數字。手機不在身邊時，也可以輸入一組備用碼。
        </div>
        <input v-model="code" name="one-time-code" autocomplete="one-time-code"
               inputmode="text" placeholder="000000 或備用碼"
               autocapitalize="characters" spellcheck="false" autofocus />
        <button class="btn" type="submit" :disabled="busy || !code">
          {{ busy ? '驗證中…' : '驗證' }}
        </button>
        <button type="button" class="linkbtn" @click="backToPassword">重新輸入密碼</button>
      </form>
      <div v-if="error" class="err">{{ error }}</div>
      <div v-else-if="store.loginNotice" class="notice">{{ store.loginNotice }}</div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  background: var(--panel);
  border: 1px solid #313148;
  border-radius: 12px;
  padding: 32px;
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
/* the artwork carries the wordmark, so there is no text to size —
   width is capped rather than fixed so a narrow window still fits it */
h1 { margin: 0 0 2px; }
.logo { display: block; width: min(260px, 70vw); height: auto; }
.sub { color: var(--dim); margin: 0 0 8px; font-size: 13px; }
.err { color: var(--danger); font-size: 13px; }
.notice { color: #6cc28a; font-size: 13px; }
.step2 { color: var(--dim); font-size: 12px; line-height: 1.6; text-align: left; }
.linkbtn { background: none; border: none; color: var(--dim); font-size: 12px;
  cursor: pointer; }
.linkbtn:hover { color: var(--accent); }
/* same column and spacing the inputs had as direct children of the card */
.login-form { display: flex; flex-direction: column; gap: 12px; margin: 0; }
</style>
