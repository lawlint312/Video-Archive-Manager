<script setup>
import { ref, computed } from 'vue'
import { api, errText } from '../api'
import { POLICY, changeProblem } from '../password'
import Icon from './Icon.vue'

const props = defineProps({
  // Set when the server said this account must pick a new password before it
  // can do anything else: there is then no way out of the dialog except
  // changing it (or signing out), because every other call returns 403.
  forced: { type: Boolean, default: false },
  reason: { type: String, default: '' },
})
const emit = defineEmits(['close', 'changed', 'logout'])

const current = ref('')
const next = ref('')
const again = ref('')
const error = ref('')
const busy = ref(false)

// Only spoken up once something has been typed in the new-password fields:
// a red message on a form nobody has touched yet reads as a scolding.
const liveProblem = computed(() =>
  (next.value || again.value) ? changeProblem(current.value || 'x', next.value, again.value) : null)

async function submit() {
  error.value = changeProblem(current.value, next.value, again.value) || ''
  if (error.value) return
  busy.value = true
  try {
    await api('/api/auth/password', { method: 'POST',
      body: JSON.stringify({ current_password: current.value,
                             new_password: next.value }) })
    // The server has already ended every session of this account, this one
    // included; App takes it from here back to the login page.
    emit('changed')
  } catch (e) {
    error.value = errText(e)
  } finally { busy.value = false }
}
</script>

<template>
  <div class="modal" @click.self="props.forced || emit('close')">
    <!-- A real form with autocomplete hints: without them a browser offers
         the saved password for the *new* fields, the same trap the account
         manager fell into. -->
    <form class="dlg" @submit.prevent="submit">
      <div class="dhead">
        <span class="ib"><Icon name="user" :size="15" /> 修改密碼</span>
        <span class="spacer"></span>
        <button v-if="!props.forced" type="button" class="btn ghost ib" @click="emit('close')">
          <Icon name="close" :size="14" /> 關閉
        </button>
      </div>

      <div v-if="props.forced" class="forced">
        這個帳號的密碼太容易被猜到，請先換一個才能繼續使用。
        <div v-if="props.reason" class="why">{{ props.reason }}</div>
      </div>

      <label class="lab">目前的密碼</label>
      <input v-model="current" type="password" name="current-password"
             autocomplete="current-password" :disabled="busy" />

      <label class="lab">新密碼</label>
      <input v-model="next" type="password" name="new-password"
             autocomplete="new-password" :disabled="busy" />
      <label class="lab">再輸入一次新密碼</label>
      <input v-model="again" type="password" name="new-password-again"
             autocomplete="new-password" :disabled="busy" />
      <div class="hint">{{ POLICY }}</div>

      <div v-if="error" class="err">{{ error }}</div>
      <div v-else-if="liveProblem" class="warn">{{ liveProblem }}</div>

      <div class="note">改好之後，這個帳號在所有裝置上的登入都會失效，需要用新密碼重新登入。</div>

      <div class="foot">
        <span class="spacer"></span>
        <button v-if="props.forced" type="button" class="btn ghost"
                :disabled="busy" @click="emit('logout')">登出</button>
        <button v-else type="button" class="btn ghost" @click="emit('close')"
                :disabled="busy">取消</button>
        <button type="submit" class="btn ib" :disabled="busy">
          <Icon v-if="busy" name="spinner" :size="13" class="spin" /> 修改密碼
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.dlg { background: var(--panel); border-radius: 10px; padding: 14px 16px;
  width: min(400px, 94vw); display: flex; flex-direction: column; gap: 6px;
  color: var(--text); font-size: 13px; }
.dhead { display: flex; align-items: center; gap: 8px; font-size: 14px; margin-bottom: 6px; }
.spacer { flex: 1; }
.lab { color: var(--dim); font-size: 12px; margin-top: 4px; }
.hint { color: var(--dim); font-size: 11px; }
.note { color: var(--dim); font-size: 11px; line-height: 1.6; margin-top: 6px; }
.err { color: var(--danger, #e66); font-size: 12px; }
.warn { color: #e9a24a; font-size: 12px; }
.forced { border: 1px solid #f38ba844; background: #f38ba814; color: #f38ba8;
  border-radius: 6px; padding: 8px 10px; font-size: 12px; line-height: 1.6; }
.forced .why { color: var(--text); margin-top: 4px; }
.foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
</style>
