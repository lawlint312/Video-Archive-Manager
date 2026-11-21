<script setup>
// A folder list in Settings (inbox folders, archive roots): one row per
// folder with whether the server can reach it, plus typing/pasting or
// browsing the server's folders to add one. Replaced a textarea per list, in
// which wrapped UNC paths made it impossible to tell what was configured.
import { ref, computed, watch } from 'vue'
import { api, errText } from '../api'
import Icon from './Icon.vue'
import { t } from '../i18n'
import {
  addFolders, moveFolder, parentFolder, startPlaces, joinFolder, folderKey,
} from '../folderlist.js'

const props = defineProps({
  modelValue: { type: Array, required: true },
  kind: { type: String, default: 'inbox' },        // 'inbox' | 'archive'
  // archive only: the folder new characters go to ('' = the first one)
  defaultRoot: { type: String, default: null },
  // every configured folder, both lists: where the browser offers to start
  allPaths: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const typed = ref('')
const note = ref('')
const removed = ref(null)          // { path, index } — for 復原
const status = ref({})             // folderKey → { ok, dirs, error } | 'busy'

const list = computed(() => props.modelValue)
const ordered = computed(() => props.kind === 'archive')

function set(v) { emit('update:modelValue', v) }

function add(text) {
  const r = addFolders(list.value, text)
  note.value = r.dupes.length
    ? t('已在清單中：{paths}', { paths: r.dupes.join('、') }) : ''
  if (r.added.length) { set(r.list); removed.value = null }
  return r.added.length > 0 || r.dupes.length > 0
}

function addTyped() {
  if (add(typed.value)) typed.value = ''
}

// a pasted block of lines (the old textarea's contents) goes in whole —
// a one-line input would otherwise glue the lines together
function onPaste(e) {
  const text = e.clipboardData?.getData('text') || ''
  if (!/\r?\n/.test(text.trim())) return
  e.preventDefault()
  add(text)
}

function remove(i) {
  removed.value = { path: list.value[i], index: i }
  note.value = ''
  set(list.value.filter((_, j) => j !== i))
}

function undo() {
  const r = removed.value
  if (!r) return
  const out = [...list.value]
  out.splice(Math.min(r.index, out.length), 0, r.path)
  set(out)
  removed.value = null
}

function isDefault(p, i) {
  if (props.kind !== 'archive') return false
  const d = props.defaultRoot
  const inList = d && list.value.some(x => folderKey(x) === folderKey(d))
  return inList ? folderKey(p) === folderKey(d) : i === 0
}

// the folder name stands out; the long part in front of it is dimmed
function split(p) {
  if (!parentFolder(p)) return { head: '', tail: p }
  const tail = p.replace(/^.*[\\/]/, '')
  return { head: p.slice(0, p.length - tail.length), tail }
}

const UNC_EXAMPLE = '\\\\NAS\\share'

// ── reachability ──────────────────────────────────────────────────────
async function check(paths) {
  const want = paths.filter(p => !status.value[folderKey(p)])
  if (!want.length) return
  for (const p of want) status.value[folderKey(p)] = 'busy'
  try {
    const r = await api('/api/settings/paths_check', {
      method: 'POST', body: JSON.stringify({ paths: want }),
    })
    for (const p of want) status.value[folderKey(p)] = r.paths[p] || { ok: false, error: '' }
  } catch (e) {
    for (const p of want) status.value[folderKey(p)] = { ok: false, error: errText(e) }
  }
}
watch(list, v => check(v), { immediate: true })

function stateOf(p) { return status.value[folderKey(p)] }

// ── server folder browser ─────────────────────────────────────────────
const browsing = ref(false)
const cur = ref('')
const curInput = ref('')
const dirs = ref([])
const more = ref(0)
const parent = ref(null)
const dirErr = ref('')
const dirBusy = ref(false)
const places = computed(() => startPlaces(props.allPaths))

async function go(p) {
  dirBusy.value = true
  dirErr.value = ''
  try {
    const r = await api('/api/settings/folders?path=' + encodeURIComponent(p || ''))
    cur.value = r.path
    curInput.value = r.path
    dirs.value = r.dirs
    more.value = r.more || 0
    parent.value = r.parent
  } catch (e) {
    dirErr.value = errText(e)
    curInput.value = p
  } finally {
    dirBusy.value = false
  }
}

function openBrowser() {
  browsing.value = !browsing.value
  if (browsing.value && !dirs.value.length && !dirErr.value) go(cur.value || places.value[0] || '')
}

function inList(p) { return list.value.some(x => folderKey(x) === folderKey(p)) }
</script>

<template>
  <div class="fle">
    <div v-if="!list.length" class="empty">{{ t('（清單是空的）') }}</div>
    <div v-for="(p, i) in list" :key="p" class="row">
      <Icon name="folder" :size="16" class="ficon" />
      <div class="ptxt">
        <div class="pname" :title="p">
          <span class="head">{{ split(p).head }}</span><b>{{ split(p).tail }}</b>
          <span v-if="isDefault(p, i)" class="badge">{{ t('新角色預設') }}</span>
        </div>
        <div class="pstat">
          <template v-if="stateOf(p) === 'busy' || !stateOf(p)">{{ t('檢查中…') }}</template>
          <span v-else-if="stateOf(p).ok" class="ok">
            <Icon name="check" :size="12" />
            {{ kind === 'archive' ? t('{n} 個角色資料夾', { n: stateOf(p).dirs }) : t('可以存取') }}
          </span>
          <span v-else class="bad">
            <Icon name="warn" :size="12" />
            {{ stateOf(p).error === 'timeout' ? t('沒有回應')
               : t('無法存取：{msg}', { msg: stateOf(p).error }) }}
          </span>
        </div>
      </div>
      <div class="acts">
        <template v-if="ordered">
          <button class="ib" :title="t('上移')" :disabled="i === 0" @click="set(moveFolder(list, i, -1))">
            <Icon name="up" :size="14" /></button>
          <button class="ib" :title="t('下移')" :disabled="i === list.length - 1" @click="set(moveFolder(list, i, 1))">
            <Icon name="down" :size="14" /></button>
        </template>
        <button class="ib del" :title="t('移除')" @click="remove(i)"><Icon name="close" :size="14" /></button>
      </div>
    </div>

    <div class="addrow">
      <input v-model="typed" :placeholder="t('輸入或貼上路徑（可一次貼多行）')"
             @keydown.enter.prevent="addTyped" @paste="onPaste" />
      <button class="b" :disabled="!typed.trim()" @click="addTyped"><Icon name="plus" :size="14" /> {{ t('加入') }}</button>
      <button class="b" :class="{ on: browsing }" @click="openBrowser"><Icon name="folderOpen" :size="14" /> {{ t('瀏覽…') }}</button>
    </div>
    <div v-if="removed" class="note">
      {{ t('已移除 {path}', { path: removed.path }) }}
      <button class="lnk2" @click="undo">{{ t('復原') }}</button>
    </div>
    <div v-if="note" class="note warnc">{{ note }}</div>

    <div v-if="browsing" class="browser">
      <div class="places">
        <button class="chip" @click="go('')"><Icon name="package" :size="12" /> {{ t('磁碟機') }}</button>
        <button v-for="pl in places" :key="pl" class="chip" :title="pl" @click="go(pl)">{{ pl }}</button>
      </div>
      <div class="bar">
        <button class="ib" :title="t('上一層')" :disabled="parent === null || dirBusy" @click="go(parent)">
          <Icon name="up" :size="14" /></button>
        <input v-model="curInput" :placeholder="t('磁碟機')" @keydown.enter.prevent="go(curInput)" />
        <button class="b" :disabled="dirBusy" @click="go(curInput)">{{ t('前往') }}</button>
      </div>
      <div v-if="dirErr" class="note bad">{{ dirErr }}</div>
      <div class="dirs">
        <div v-if="dirBusy" class="empty">{{ t('載入中…') }}</div>
        <template v-else>
          <div v-if="!dirs.length && !dirErr" class="empty">{{ t('沒有子資料夾') }}</div>
          <div v-for="d in dirs" :key="d" class="drow" @click="go(joinFolder(cur, d))">
            <Icon name="folder" :size="14" class="ficon" />
            <span class="dn">{{ d }}</span>
            <button v-if="!inList(joinFolder(cur, d))" class="ib add" :title="t('加入')"
                    @click.stop="add(joinFolder(cur, d))"><Icon name="plus" :size="14" /></button>
            <Icon v-else name="check" :size="14" class="ok" />
          </div>
          <div v-if="more" class="empty">{{ t('還有 {n} 個沒列出', { n: more }) }}</div>
        </template>
      </div>
      <div class="bfoot">
        <span class="hint">{{ t('NAS 的共用資料夾要先在上面輸入一次，例如 {ex}', { ex: UNC_EXAMPLE }) }}</span>
        <button class="b pri" :disabled="!cur || dirBusy || !!dirErr || inList(cur)" @click="add(cur)">
          {{ inList(cur) ? t('已在清單中') : t('加入這個資料夾') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fle { display: flex; flex-direction: column; gap: 4px; }
.row { display: flex; align-items: center; gap: 10px; padding: 7px 8px;
  border: 1px solid var(--line, #313148); border-radius: 8px; background: #ffffff05; }
.ficon { color: var(--accent); }
.ptxt { flex: 1 1 auto; min-width: 0; }
.pname { font-size: 13px; word-break: break-all; line-height: 1.45; }
.pname .head { color: var(--dim); }
.pname b { font-weight: 600; color: var(--text); }
.badge { display: inline-block; margin-left: 8px; font-size: 10px; padding: 1px 6px;
  border-radius: 8px; background: #7c7cf033; color: var(--accent); vertical-align: 1px;
  word-break: keep-all; }
.pstat { font-size: 11px; color: var(--dim); margin-top: 2px; }
.ok { color: #a6e3a1; }
.bad { color: var(--danger); }
.acts { display: flex; gap: 2px; flex: 0 0 auto; }
.ib { background: none; border: none; color: var(--dim); padding: 4px; border-radius: 6px;
  cursor: pointer; display: inline-flex; }
.ib:hover:not(:disabled) { background: #ffffff14; color: var(--text); }
.ib:disabled { opacity: .3; cursor: default; }
.ib.del:hover { color: var(--danger); }
.addrow, .bar { display: flex; gap: 6px; margin-top: 4px; }
.addrow input, .bar input { flex: 1 1 auto; min-width: 0; }
.b { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 4px; white-space: nowrap;
  background: #313148; color: var(--text); border: none; border-radius: 6px;
  padding: 0 10px; font-size: 12px; cursor: pointer; }
.b:hover:not(:disabled) { background: #3d3d5c; }
.b:disabled { opacity: .45; cursor: default; }
.b.on, .b.pri { background: var(--accent); color: #fff; }
.b.pri { padding: 6px 12px; }
.empty { color: var(--dim); font-size: 12px; padding: 6px 2px; }
.note { font-size: 11px; color: var(--dim); }
.note.warnc { color: #f9e2af; }
.lnk2 { background: none; border: none; color: var(--accent); font-size: 11px; cursor: pointer; padding: 0 4px; }
.browser { margin-top: 6px; border: 1px solid var(--line, #313148); border-radius: 8px;
  padding: 8px; display: flex; flex-direction: column; gap: 6px; background: #00000022; }
.places { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { background: #313148; color: var(--text); border: none; border-radius: 12px;
  font-size: 11px; padding: 3px 9px; cursor: pointer; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex;
  align-items: center; gap: 4px; }
.chip:hover { background: #3d3d5c; }
.dirs { max-height: 240px; overflow-y: auto; border-radius: 6px; }
.drow { display: flex; align-items: center; gap: 8px; padding: 5px 6px; border-radius: 6px;
  cursor: pointer; font-size: 13px; }
.drow:hover { background: #ffffff0d; }
.dn { flex: 1 1 auto; min-width: 0; word-break: break-all; }
.bfoot { display: flex; align-items: center; gap: 8px; justify-content: space-between; flex-wrap: wrap; }
.hint { font-size: 11px; color: var(--dim); flex: 1 1 200px; }
</style>
