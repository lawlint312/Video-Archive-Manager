<script setup>
// Under a 「載入中…」 that takes long: which root folder the server is
// waiting on. Opening VAM after the NAS had idled for an hour and a half sat
// there for over a minute with nothing to say why (2026-11-18). Nothing shows
// for the first 1.5 s — a normal load is over by then.
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { t } from '../i18n'

const p = ref(null)
let timer = null
let stopped = false

async function poll() {
  try {
    p.value = (await api('/api/roots/progress')).progress
  } catch {
    stopped = true          // signed out, or an older server without it
    return
  }
  if (!stopped) timer = setTimeout(poll, 800)
}

onMounted(() => { timer = setTimeout(poll, 1500) })
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })

function label(x) {
  const n = { done: x.done + 1, total: x.total }
  return x.kind === 'list' ? t('讀取角色資料夾 {done}/{total}', n)
                           : t('確認根目錄 {done}/{total}', n)
}
</script>

<template>
  <div v-if="p && p.waited >= 1" class="rootwait">
    <div>{{ label(p) }} · {{ t('已等 {s} 秒', { s: Math.round(p.waited) }) }}</div>
    <div v-if="p.current" class="cur" :title="p.current">{{ p.current }}</div>
  </div>
</template>

<style scoped>
.rootwait { margin-top: 10px; font-size: 12px; color: var(--dim); line-height: 1.6; }
.cur { word-break: break-all; opacity: .85; }
</style>
