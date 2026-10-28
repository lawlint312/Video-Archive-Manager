<script setup>
/**
 * The progress block for a long scan: which phase, how far, how much longer,
 * and a way out.
 *
 * Shared by both duplicate scans rather than duplicated into each dialog —
 * the two behave identically here, and a stop button that exists in one and
 * not the other is the kind of difference nobody notices until they need it.
 *
 * The bar is per phase, not for the whole scan; `label` says which phase, and
 * `phase k/n` says where that sits. A single overall bar would have to weight
 * phases whose per-item costs differ by orders of magnitude (reading two
 * chunks of a file vs decoding frames), and would crawl then sprint.
 */
import { pct, fmtEta, fmtProgressCount } from '../scanprogress'
import Icon from './Icon.vue'

const props = defineProps({
  progress: { type: [Object, String], default: null },
  stopping: { type: Boolean, default: false },
  // shown while the server has not sent its first update yet
  starting: { type: String, default: '啟動中…' },
})
defineEmits(['stop'])

const p = () => (props.progress && typeof props.progress === 'object'
  ? props.progress : null)
</script>

<template>
  <div class="prog">
    <div class="top">
      <Icon name="spinner" :size="13" class="spin" />
      <template v-if="p()">
        <span v-if="p().phases > 1" class="ph">階段 {{ p().phase }}/{{ p().phases }}</span>
        <b>{{ p().label }}</b>
        <span class="cnt">{{ fmtProgressCount(p()) }}</span>
        <span v-if="p().hint" class="hint">（{{ p().hint }}）</span>
        <span class="spacer"></span>
        <!-- Only appears once the estimate is worth showing; a placeholder
             like "計算中" would just be a second thing to wait for. -->
        <span v-if="fmtEta(p().eta)" class="eta">{{ fmtEta(p().eta) }}</span>
      </template>
      <template v-else>
        <span>{{ typeof progress === 'string' && progress ? progress : starting }}</span>
        <span class="spacer"></span>
      </template>
      <button class="stop" :disabled="stopping" @click="$emit('stop')">
        {{ stopping ? '停止中…' : '停止' }}
      </button>
    </div>

    <div class="bar" :class="{ idle: pct(p()) === null }">
      <div class="fill" :style="{ width: (pct(p()) ?? 100) + '%' }"></div>
    </div>

    <div class="foot">
      <span class="name">{{ p()?.name || '' }}</span>
      <span class="spacer"></span>
      <span class="bg">掃描在背景進行，可關閉此視窗繼續使用</span>
    </div>
    <div v-if="stopping" class="stopnote">
      正在收尾：手上這個檔案跑完就停，已經找到的結果會保留。
    </div>
  </div>
</template>

<style scoped>
.prog { padding: 10px 16px 12px; font-size: 12px; color: var(--dim); }
.top { display: flex; align-items: center; gap: 8px; }
.top b { color: var(--text); font-weight: 600; }
.spacer { flex: 1; }
.ph { background: var(--card); border: 1px solid #555; border-radius: 3px;
  padding: 1px 6px; font-size: 11px; }
.cnt { font-variant-numeric: tabular-nums; color: var(--text); }
.hint { font-size: 11px; }
.eta { color: #f9e2af; font-variant-numeric: tabular-nums; }
.stop { background: var(--card); border: 1px solid #666; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 12px; cursor: pointer; }
.stop:hover:not(:disabled) { border-color: #f38ba8; color: #f38ba8; }
.stop:disabled { opacity: .55; cursor: default; }

.bar { height: 5px; margin: 8px 0 6px; border-radius: 3px;
  background: #ffffff14; overflow: hidden; }
.fill { height: 100%; background: var(--accent); border-radius: 3px;
  transition: width .3s ease; }
/* No total yet (the folder walk): a full bar sliding past says "working"
   without claiming a position it does not know. */
.bar.idle .fill { background: linear-gradient(90deg,
  transparent, var(--accent), transparent); background-size: 40% 100%;
  background-repeat: no-repeat; animation: sweep 1.4s linear infinite; }
@keyframes sweep { from { background-position: -40% 0 } to { background-position: 140% 0 } }

.foot { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 55%; }
.bg { color: #f9e2af; }
.stopnote { margin-top: 6px; font-size: 11px; color: #f9e2af; }
.spin { animation: spin 1s linear infinite; flex: none; }
@keyframes spin { to { transform: rotate(360deg) } }
</style>
