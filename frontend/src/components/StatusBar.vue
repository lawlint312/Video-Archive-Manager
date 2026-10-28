<script setup>
import { store } from '../store'
import Icon from './Icon.vue'
</script>

<template>
  <div class="statusbar">
    <span class="msg ib" :class="store.statusKind" :title="store.status">
      <Icon v-if="store.status" :name="store.statusKind === 'warn' ? 'warn' : 'check'" :size="13" />
      {{ store.status || '就緒' }}
    </span>

    <span v-if="store.busyText" class="busy ib" :title="store.busyText">
      <Icon name="spinner" :size="13" class="spin" /> {{ store.busyText }}
    </span>

    <span class="spacer"></span>

    <span v-if="store.archived.length" class="count">
      本次已歸檔 <b>{{ store.archived.length }}</b> 部
    </span>
  </div>
</template>

<style scoped>
.statusbar {
  display: flex; align-items: center; gap: 14px;
  height: 26px; padding: 0 14px;
  background: var(--panel); border-top: 1px solid #313148;
  font-size: 11px; color: var(--dim);
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 40;
}
.msg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 45%; }
.msg.ok { color: var(--success); }
.msg.warn { color: var(--danger); }
.busy { color: #f9e2af; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; max-width: 40%; }
.spin { animation: rot 1s linear infinite; }
@keyframes rot { to { transform: rotate(360deg); } }
.spacer { flex: 1; }
.count b { color: var(--text); }
</style>
