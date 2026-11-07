<script setup>
/**
 * Folder range picker shared by the duplicate scan, the converter and the
 * image scan.
 *
 * `defaultAll` sets which way round the list starts. Every caller now starts
 * empty — you name what you want each time — but the flag stays because the
 * stored set is the folders that *differ* from the default, so a folder added
 * later inherits the dialog's own default rather than silently appearing in
 * (or missing from) an old saved selection.
 *
 * The library reached 766 rows across 12 roots, which made finding one folder
 * a matter of scrolling ~76 screens. Hence the search box, the per-root chips
 * and the "only what I picked" view. Filtering never touches the selection —
 * it only changes what you are looking at.
 */
import { ref, computed, onMounted, watch } from 'vue'
import { api, errText } from '../api'
import { isPicked, togglePath, applyAll, visibleItems,
         rootCounts } from '../scopefilter'
import Icon from './Icon.vue'
import { t } from '../i18n'

const props = defineProps({
  storageKey: { type: String, required: true },   // per-dialog memory
  disabled: { type: Boolean, default: false },
  defaultAll: { type: Boolean, default: true },   // start with everything on?
  // Should a selection outlive the dialog closing?
  //
  // The converter says yes: you pick a target folder and come back to it. The
  // duplicate scans say no. A narrowing made for one job ("just tidy
  // 0cmspring_Mia") silently survived into the next one — you reopen, the
  // picker is collapsed to a summary, and nothing tells you the next scan is
  // still scoped to one folder unless you expand the list and look.
  remember: { type: Boolean, default: true },
  // the face index works on character folders only: no 待分類 section
  showInbox: { type: Boolean, default: true },
  // optional note per row, {path: {text, title, dim}} — the face index puts
  // "N left" there, so the choice is made knowing what each folder costs
  info: { type: Object, default: null },
})
const emit = defineEmits(['update:selected'])

const inbox = ref([])
const chars = ref([])
const error = ref('')
// paths whose state differs from `defaultAll`
const flipped = ref(new Set(load()))

const q = ref('')
const rootFilter = ref('')       // '' = every root
const onlySelected = ref(false)

function load() {
  if (!props.remember) return []
  try { return JSON.parse(localStorage.getItem(props.storageKey) || '[]') }
  catch { return [] }
}
function persist() {
  if (!props.remember) return
  localStorage.setItem(props.storageKey, JSON.stringify([...flipped.value]))
}

// All of this lives in ../scopefilter so it can be tested without a browser.
function picked(p) { return isPicked(flipped.value, p, props.defaultAll) }
function toggle(p) { flipped.value = togglePath(flipped.value, p) }
function setAll(list, on) {
  flipped.value = applyAll(flipped.value, list, on, props.defaultAll)
}

function opts(useRoot) {
  return { q: q.value, rootFilter: rootFilter.value,
           onlySelected: onlySelected.value, picked, useRoot }
}
const shownInbox = computed(() => visibleItems(inbox.value, opts(false)))
const shownChars = computed(() => visibleItems(chars.value, opts(true)))
const roots = computed(() => rootCounts(chars.value))

const filtering = computed(() =>
  !!q.value.trim() || !!rootFilter.value || onlySelected.value)

function clearFilters() {
  q.value = ''
  rootFilter.value = ''
  onlySelected.value = false
}

const all = computed(() => [...inbox.value, ...chars.value])
const selected = computed(() => all.value.map(i => i.path).filter(picked))
const inboxOn = computed(() => inbox.value.filter(i => picked(i.path)).length)
const charsOn = computed(() => chars.value.filter(c => picked(c.path)).length)

watch(selected, v => { persist(); emit('update:selected', v) }, { immediate: true })

onMounted(async () => {
  // Drop whatever an earlier build of this dialog saved, so a selection from
  // before `remember: false` cannot come back if the flag is ever flipped.
  if (!props.remember) localStorage.removeItem(props.storageKey)
  try {
    const s = await api('/api/scope')
    inbox.value = props.showInbox ? s.inbox : []
    chars.value = s.chars
    emit('update:selected', selected.value)
  } catch (e) {
    error.value = t('無法載入資料夾清單：') + errText(e)
  }
})

// tick every listed folder `pred` accepts (and only those), e.g. the ones
// with something left to do
function selectWhere(pred) {
  const on = chars.value.filter(c => pred(c.path))
  flipped.value = applyAll(applyAll(flipped.value, chars.value, false, props.defaultAll),
                           on, true, props.defaultAll)
}

defineExpose({ inboxOn, charsOn, selected, selectWhere })
</script>

<template>
  <div class="scope">
    <div v-if="error" class="err">{{ error }}</div>

    <div class="tally" :class="{ none: !selected.length }">
      <Icon :name="selected.length ? 'checked' : 'warn'" :size="13" />
      {{ t('已選') }} <b>{{ selected.length }}</b> {{ t('/ {n} 個資料夾', { n: all.length }) }}
      <span v-if="!selected.length" class="hint">{{ t('— 請勾選要處理的資料夾') }}</span>
      <span class="spacer"></span>
      <button class="lnk" :class="{ on: onlySelected }"
              @click="onlySelected = !onlySelected">
        {{ onlySelected ? t('顯示全部') : t('只看已選') }}
      </button>
    </div>

    <!-- search -->
    <div class="search">
      <Icon name="search" :size="13" />
      <input v-model="q" :disabled="disabled" :placeholder="t('搜尋名稱或路徑…')" />
      <button v-if="q" class="x" @click="q = ''" :title="t('清除')">×</button>
      <span v-if="filtering" class="hint">
        {{ t('顯示 {n} 個', { n: shownChars.length + shownInbox.length }) }}
      </span>
      <button v-if="filtering" class="lnk" @click="clearFilters">{{ t('清除篩選') }}</button>
    </div>

    <!-- per-root chips -->
    <div v-if="roots.length > 1" class="chips">
      <button class="chip" :class="{ on: !rootFilter }" @click="rootFilter = ''">
        {{ t('全部 {n}', { n: chars.length }) }}
      </button>
      <button v-for="r in roots" :key="r.label" class="chip"
              :class="{ on: rootFilter === r.label }"
              @click="rootFilter = rootFilter === r.label ? '' : r.label">
        {{ r.label }} {{ r.n }}
      </button>
    </div>

    <section v-if="shownInbox.length">
      <div class="shead">
        <span class="ib"><Icon name="inbox" :size="14" /> {{ t('待分類資料夾') }}</span>
        <span class="allbtns">
          <button class="lnk" @click="setAll(shownInbox, true)">{{ t('全選') }}</button>
          <button class="lnk" @click="setAll(shownInbox, false)">{{ t('全不選') }}</button>
        </span>
      </div>
      <label v-for="i in shownInbox" :key="i.path" class="row-chk">
        <input type="checkbox" :checked="picked(i.path)" :disabled="disabled"
               @change="toggle(i.path)" />
        <span class="p">{{ i.path }}</span>
      </label>
    </section>

    <section>
      <div class="shead">
        <span class="ib"><Icon name="folder" :size="14" /> {{ t('歸檔資料夾') }}</span>
        <!-- the counts are load-bearing: with a filter on, these buttons act
             on what is shown, and silently selecting all 766 would be bad -->
        <span class="allbtns">
          <span v-if="filtering" class="scopehint">{{ t('（僅作用於顯示中的）') }}</span>
          <button class="lnk" @click="setAll(shownChars, true)">
            {{ t('全選 {n}', { n: shownChars.length }) }}
          </button>
          <button class="lnk" @click="setAll(shownChars, false)">
            {{ t('全不選 {n}', { n: shownChars.length }) }}
          </button>
        </span>
      </div>
      <div class="charbox">
        <label v-for="c in shownChars" :key="c.path" class="row-chk">
          <input type="checkbox" :checked="picked(c.path)" :disabled="disabled"
                 @change="toggle(c.path)" />
          <span class="nm">{{ c.name }}</span>
          <span v-if="info && info[c.path]" class="inf" :class="{ dim: info[c.path].dim }"
                :title="info[c.path].title || ''">{{ info[c.path].text }}</span>
          <!-- the same person often exists on several drives; the badge is
               what tells those rows apart, which a truncated path did not -->
          <span class="root" :title="c.path">{{ c.root_label }}</span>
        </label>
        <div v-if="!chars.length" class="dimline">{{ t('（沒有歸檔資料夾）') }}</div>
        <div v-else-if="!shownChars.length" class="dimline">
          {{ onlySelected && !q && !rootFilter
             ? t('目前沒有勾選任何歸檔資料夾。')
             : t('沒有符合的資料夾。') }}
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.scope section { margin-bottom: 12px; }
.spacer { flex: 1; }
.tally { display: flex; align-items: center; gap: 6px; font-size: 12px;
  margin-bottom: 8px; color: var(--text); }
.tally b { color: var(--accent); }
.tally.none { color: #f9e2af; }
.tally.none b { color: #f9e2af; }
.tally .hint { color: var(--dim); font-size: 11px; }
.search { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.search input { flex: 1; background: #1b1b2a; border: 1px solid #313148;
  color: var(--text); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
.search input:focus { outline: none; border-color: var(--accent); }
.search .x { background: none; border: none; color: var(--dim); cursor: pointer;
  font-size: 15px; line-height: 1; padding: 0 2px; }
.search .hint { color: var(--dim); font-size: 11px; white-space: nowrap; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.chip { background: transparent; border: 1px solid #313148; color: var(--dim);
  border-radius: 999px; padding: 2px 9px; font-size: 11px; cursor: pointer; }
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.shead { display: flex; align-items: center; justify-content: space-between;
  font-size: 12px; color: var(--dim); margin-bottom: 6px; }
.allbtns { display: flex; gap: 10px; align-items: center; }
.scopehint { font-size: 10px; color: var(--dim); }
.lnk { background: none; border: none; color: var(--dim); font-size: 11px;
  cursor: pointer; padding: 0; }
.lnk:hover { color: var(--accent); text-decoration: underline; }
.lnk.on { color: var(--accent); }
.charbox { max-height: 40vh; max-height: 40dvh; min-height: 120px; overflow-y: auto;
  border: 1px solid #313148; border-radius: 6px; padding: 6px 8px; }
.row-chk { display: flex; align-items: center; gap: 8px; font-size: 12px;
  line-height: 19px; padding: 1px 0; cursor: pointer; }
.row-chk .nm { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.row-chk .inf { flex: 0 0 auto; color: #f9e2af; font-size: 11px; }
.row-chk .inf.dim { color: var(--dim); }
.row-chk .root { flex: 0 0 auto; color: var(--dim); font-size: 10px;
  border: 1px solid #313148; border-radius: 4px; padding: 0 5px; }
.row-chk .p { color: var(--dim); font-size: 11px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.dimline { color: var(--dim); font-size: 12px; padding: 4px 2px; }
.err { color: var(--danger); font-size: 12px; margin-bottom: 8px; }
</style>
