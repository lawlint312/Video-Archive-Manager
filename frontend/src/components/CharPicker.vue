<script setup>
/**
 * Pick characters, grouped by sidebar category.
 *
 * Separate from ScopePicker because the unit differs: that one selects
 * folders (paths) grouped by drive, this one selects people grouped by the
 * category they were filed under. The filtering behaviour is deliberately
 * identical — same search, same "only what I picked", and select-all acts
 * only on what is shown.
 *
 * Nothing starts selected: this drives network lookups against a third-party
 * site, so it must be a deliberate choice, never a default.
 */
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { matchesQuery } from '../scopefilter'
import Icon from './Icon.vue'

const props = defineProps({
  storageKey: { type: String, default: 'vam.charpicker' },
  disabled: { type: Boolean, default: false },
  // Which feature's progress the badges report. The picker is shared, and
  // showing profile completion inside the title dialog told the user 34
  // characters were done when they had run one.
  // 'plain': no progress to report (the NFO tab) — no badges, no state chips
  mode: { type: String, default: 'profile' },   // 'profile' | 'title' | 'plain'
  // names already chosen: the picker is re-mounted each time it is opened
  // again, and without this it would show nothing ticked while the parent
  // still holds (and would run on) the earlier choice
  initial: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:selected'])

const chars = ref([])
const order = ref([])
const error = ref('')
const chosen = ref(new Set(props.initial))

const q = ref('')
const groupFilter = ref('')
const stateFilter = ref('')      // '' | done | todo
const onlySelected = ref(false)

// "todo" folds in the ones a previous run could not resolve: adding films to
// a folder can make it resolvable, so they are worth re-offering rather than
// being parked forever.
const LABELS = {
  profile: { done: '已完成', rejected: '先前已否決', unresolved: '上次無法判斷' },
  title: { done: '已完成', partial: '部分已有' },
}
function stateOf(c) {
  return props.mode === 'title' ? (c.title_state || 'none') : c.state
}
const STATE_LABEL = computed(() => LABELS[props.mode] || LABELS.profile)
function isTodo(c) { return stateOf(c) !== 'done' }

function toggle(name) {
  const s = new Set(chosen.value)
  s.has(name) ? s.delete(name) : s.add(name)
  chosen.value = s
  emit('update:selected', [...s])
}
function setAll(list, on) {
  const s = new Set(chosen.value)
  for (const c of list) on ? s.add(c.name) : s.delete(c.name)
  chosen.value = s
  emit('update:selected', [...s])
}

const shown = computed(() => chars.value.filter(c => {
  if (groupFilter.value && c.group !== groupFilter.value) return false
  if (stateFilter.value === 'done' && stateOf(c) !== 'done') return false
  if (stateFilter.value === 'todo' && !isTodo(c)) return false
  if (onlySelected.value && !chosen.value.has(c.name)) return false
  return matchesQuery({ name: c.name, path: '' }, q.value)
}))

const doneCount = computed(() =>
  chars.value.filter(c => stateOf(c) === 'done').length)
const todoCount = computed(() => chars.value.filter(isTodo).length)

/** The whole point of the state chips: pick up where you left off. */
function selectTodo() {
  const s = new Set(chosen.value)
  for (const c of shown.value) if (isTodo(c)) s.add(c.name)
  chosen.value = s
  emit('update:selected', [...s])
}

const groups = computed(() => {
  const m = new Map()
  for (const c of chars.value) m.set(c.group, (m.get(c.group) || 0) + 1)
  return (order.value.filter(g => m.has(g)))
    .map(g => ({ name: g, n: m.get(g) }))
})

const sections = computed(() => {
  const m = new Map()
  for (const c of shown.value) {
    if (!m.has(c.group)) m.set(c.group, [])
    m.get(c.group).push(c)
  }
  return order.value.filter(g => m.has(g)).map(g => ({ name: g, members: m.get(g) }))
})

const filtering = computed(() =>
  !!q.value.trim() || !!groupFilter.value || !!stateFilter.value
  || onlySelected.value)

onMounted(async () => {
  try {
    const d = await api('/api/charscope')
    chars.value = d.chars
    order.value = d.order
  } catch (e) {
    error.value = '無法載入角色清單：' + errText(e)
  }
})
</script>

<template>
  <div class="cp">
    <div v-if="error" class="err">{{ error }}</div>

    <div class="tally" :class="{ none: !chosen.size }">
      <Icon :name="chosen.size ? 'checked' : 'warn'" :size="13" />
      已選 <b>{{ chosen.size }}</b> / {{ chars.length }} 位角色
      <span class="spacer"></span>
      <button class="lnk" :class="{ on: onlySelected }"
              @click="onlySelected = !onlySelected">
        {{ onlySelected ? '顯示全部' : '只看已選' }}
      </button>
    </div>

    <div class="search">
      <Icon name="search" :size="13" />
      <input v-model="q" :disabled="disabled" placeholder="搜尋角色…" />
      <button v-if="q" class="x" @click="q = ''">×</button>
      <span v-if="filtering" class="hint">顯示 {{ shown.length }} 位</span>
    </div>

    <div v-if="mode !== 'plain'" class="chips">
      <button class="chip st" :class="{ on: stateFilter === 'todo' }"
              @click="stateFilter = stateFilter === 'todo' ? '' : 'todo'">
        未處理 {{ todoCount }}
      </button>
      <button class="chip st" :class="{ on: stateFilter === 'done' }"
              @click="stateFilter = stateFilter === 'done' ? '' : 'done'">
        已完成 {{ doneCount }}
      </button>
      <button class="lnk sel" @click="selectTodo">全選未處理</button>
    </div>

    <div class="chips">
      <button class="chip" :class="{ on: !groupFilter }" @click="groupFilter = ''">
        全部 {{ chars.length }}
      </button>
      <button v-for="g in groups" :key="g.name" class="chip"
              :class="{ on: groupFilter === g.name }"
              @click="groupFilter = groupFilter === g.name ? '' : g.name">
        {{ g.name }} {{ g.n }}
      </button>
    </div>

    <!-- 未分類 is where the catalogue-number detection goes wrong: names like
         guoguo-77 [SC] parse as GUOGUO-077 and would send us looking up a
         webcam folder as if it were a film -->
    <div v-if="groupFilter === '未分類' || (!groupFilter && !q)" class="warnbox">
      <Icon name="warn" :size="13" />
      「未分類」混有大量非 JAV 角色，番號會誤判（例如 <code>guoguo-77 [SC]</code>
      被當成 <code>GUOGUO-077</code>）。建議只挑確定是 JAV 演員的。
    </div>

    <div class="box">
      <template v-for="s in sections" :key="s.name">
        <div class="ghdr">
          {{ s.name }} <span class="n">({{ s.members.length }})</span>
          <span class="spacer"></span>
          <button class="lnk" @click="setAll(s.members, true)">全選 {{ s.members.length }}</button>
          <button class="lnk" @click="setAll(s.members, false)">全不選</button>
        </div>
        <label v-for="c in s.members" :key="c.name" class="row">
          <input type="checkbox" :checked="chosen.has(c.name)" :disabled="disabled"
                 @change="toggle(c.name)" />
          <span class="nm">{{ c.name }}</span>
          <span v-if="mode !== 'plain' && STATE_LABEL[stateOf(c)]" class="badge" :class="stateOf(c)">
            {{ STATE_LABEL[stateOf(c)] }}
          </span>
        </label>
      </template>
      <div v-if="!sections.length" class="dim">沒有符合的角色。</div>
    </div>
  </div>
</template>

<style scoped>
.spacer { flex: 1; }
.tally { display: flex; align-items: center; gap: 6px; font-size: 12px;
  margin-bottom: 8px; }
.tally b { color: var(--accent); }
.tally.none { color: #f9e2af; }
.search { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.search input { flex: 1; background: #1b1b2a; border: 1px solid #313148;
  color: var(--text); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
.search .x { background: none; border: none; color: var(--dim); cursor: pointer; }
.hint { color: var(--dim); font-size: 11px; white-space: nowrap; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.chip { background: transparent; border: 1px solid #313148; color: var(--dim);
  border-radius: 999px; padding: 2px 9px; font-size: 11px; cursor: pointer; }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.warnbox { display: flex; align-items: flex-start; gap: 6px; font-size: 11px;
  color: #f9e2af; background: rgba(249,226,175,.08); border-radius: 6px;
  padding: 6px 8px; margin-bottom: 8px; line-height: 1.6; }
.warnbox code { background: rgba(0,0,0,.3); padding: 0 3px; border-radius: 3px; }
.box { max-height: 40vh; max-height: 40dvh; overflow-y: auto; border: 1px solid #313148;
  border-radius: 6px; padding: 6px 8px; }
.ghdr { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--dim); margin: 6px 0 3px; border-bottom: 1px solid #262636; }
.row { display: flex; align-items: center; gap: 8px; font-size: 12px;
  line-height: 19px; cursor: pointer; }
.badge { font-size: 10px; border-radius: 4px; padding: 0 6px; }
.badge.done { background: #1e3a2a; color: #7ddba0; }
.badge.rejected { background: #3a2a1e; color: #e9b949; }
.badge.unresolved { background: #262636; color: var(--dim); }
.badge.partial { background: #2a2a3e; color: #9aa4d0; }
.chip.st { border-color: #3a3a55; }
.lnk.sel { margin-left: 4px; }
.lnk { background: none; border: none; color: var(--dim); font-size: 11px;
  cursor: pointer; padding: 0; }
.lnk:hover, .lnk.on { color: var(--accent); }
.dim { color: var(--dim); font-size: 12px; padding: 6px 2px; }
.err { color: var(--danger); font-size: 12px; margin-bottom: 8px; }
</style>
