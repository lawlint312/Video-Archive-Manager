<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, errText } from '../api'
import { applyGroups } from '../groups'
import { RATIOS, RATIO_KEYS, HOME_SHAPES, HOME_KEYS, usesFolderPoster, viewOf,
         packView } from '../groupview'
import Icon from './Icon.vue'

const emit = defineEmits(['close', 'saved'])

const order = ref([])          // group names, in display order
const groups = ref({})         // { group: [charName, ...] }
const view = ref({})           // { group: {videos, profile, ratio, poster} }
const chars = ref([])          // all character names
const selected = ref(null)     // selected group in the left list
const search = ref('')
const loading = ref(true)
const error = ref('')

// character → group (''= 未分類), derived from groups
const assign = ref({})

const filteredChars = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? chars.value.filter(c => c.toLowerCase().includes(q)) : chars.value
})

// ── per-group display settings ────────────────────────────────────────────
// Held expanded (every key present) while the dialog is open so the checkboxes
// have something to bind to; `packView` drops everything equal to the default
// again on save, which is what keeps a settings file written before this
// feature existed working with no migration.
const cur = computed(() => viewOf(selected.value, view.value))

function setView(patch) {
  if (!selected.value) return
  const next = { ...viewOf(selected.value, view.value), ...patch }
  // The last visible tab cannot be turned off: a character page with neither
  // tab is a blank screen with no way back to the other one. Turning the other
  // one on is what the user meant anyway, so do that instead of refusing.
  if (!next.videos && !next.profile) {
    next[patch.videos === false ? 'profile' : 'videos'] = true
  }
  view.value = { ...view.value, [selected.value]: next }
}

// Only meaningful when a card can show a picture at all.
const albumHint = computed(() =>
  cur.value.albums
    ? '每個子資料夾一張封面卡片，點進去看那一組的照片與影片。'
      + '有照片的子資料夾少於 2 個的角色仍用平鋪。'
    : '所有子資料夾的照片攤成一片，依排序顯示。')
const posterHint = computed(() =>
  cur.value.poster ? '同目錄下與影片同名的圖片會取代抽幀縮圖；沒有的仍用抽幀。'
                   : '一律使用抽幀縮圖。')

// The folder cover rides on the shape rather than having its own checkbox:
// a round tile is small and neutral enough that the person icon reads as
// "nobody chose a photo", where an empty rectangle just reads as broken.
// 89% of characters have no 大頭照, so this is the common case, not an edge.
const homeHint = computed(() =>
  usesFolderPoster(cur.value.home)
    ? '沒有大頭照時，改用角色資料夾第一層的封面圖'
      + '（<資料夾名>_poster、poster、folder、cover），個人資料頁的頭像也一樣。'
    : '沒有大頭照時顯示人像佔位圖。')

async function load() {
  try {
    const [g, c] = await Promise.all([api('/api/groups'), api('/api/characters')])
    order.value = g.order || []
    groups.value = g.groups || {}
    view.value = g.view || {}
    chars.value = (c.characters || []).map(x => x.name)
    const a = {}
    for (const [gn, members] of Object.entries(groups.value))
      for (const m of members) a[m] = gn
    assign.value = a
    if (order.value.length) selected.value = order.value[0]
  } catch (e) {
    error.value = '載入失敗：' + errText(e)
  } finally {
    loading.value = false
  }
}

// ── group operations ──────────────────────────────────────────────────────
function addGroup() {
  const name = prompt('分類名稱：')
  if (!name || !name.trim()) return
  const n = name.trim()
  if (order.value.includes(n)) { alert(`分類「${n}」已存在`); return }
  order.value.push(n)
  groups.value[n] = []
  selected.value = n
}

function renameGroup() {
  if (!selected.value) return
  const old = selected.value
  const name = prompt('新名稱：', old)
  if (!name || !name.trim() || name === old) return
  const n = name.trim()
  if (order.value.includes(n)) { alert(`分類「${n}」已存在`); return }
  order.value[order.value.indexOf(old)] = n
  groups.value[n] = groups.value[old] || []
  delete groups.value[old]
  // The display settings are keyed by name, so a rename that forgets them
  // silently resets the group to 16:9 with both tabs on.
  if (view.value[old]) {
    view.value = { ...view.value, [n]: view.value[old] }
    delete view.value[old]
  }
  for (const [c, g] of Object.entries(assign.value)) if (g === old) assign.value[c] = n
  selected.value = n
}

function deleteGroup() {
  if (!selected.value) return
  const g = selected.value
  const members = groups.value[g] || []
  let msg = `刪除分類「${g}」？`
  if (members.length) msg += `\n\n此分類下的 ${members.length} 個角色將移至「未分類」`
  if (!confirm(msg)) return
  order.value.splice(order.value.indexOf(g), 1)
  delete groups.value[g]
  for (const [c, gg] of Object.entries(assign.value)) if (gg === g) assign.value[c] = ''
  selected.value = order.value[0] || null
}

function move(delta) {
  if (!selected.value) return
  const i = order.value.indexOf(selected.value)
  const j = i + delta
  if (j < 0 || j >= order.value.length) return
  const [item] = order.value.splice(i, 1)
  order.value.splice(j, 0, item)
}

// ── save ──────────────────────────────────────────────────────────────────
async function save() {
  // rebuild groups from the assignment map
  const out = {}
  for (const g of order.value) out[g] = []
  for (const [c, g] of Object.entries(assign.value)) {
    if (g && out[g]) out[g].push(c)
  }
  try {
    const d = await api('/api/groups', {
      method: 'POST',
      body: JSON.stringify({ groups: out, order: order.value,
                             view: packView(view.value) }),
    })
    // Push straight into the shared state as well as remounting the sidebar:
    // App.vue reads it to decide which tabs to draw, and the character the
    // user has open right now must not keep the old shape until a reload.
    applyGroups(d)
    emit('saved')
    emit('close')
  } catch (e) {
    alert('儲存失敗：' + errText(e))
  }
}

onMounted(load)
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <div class="dlg">
      <div class="dhead">
        <span class="ib"><Icon name="tags" :size="16" /> 管理分類</span>
        <button class="btn ghost" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div v-if="loading" class="msg">載入中…</div>
      <div v-else-if="error" class="msg err">{{ error }}</div>
      <div v-else class="body">
        <!-- left: group list -->
        <div class="left">
          <div class="lab">分類列表</div>
          <div class="glist">
            <div v-for="g in order" :key="g" class="gitem"
                 :class="{ on: selected === g }" @click="selected = g">
              {{ g }}
              <span class="cnt">{{ (Object.values(assign).filter(x => x === g)).length }}</span>
            </div>
            <div v-if="!order.length" class="none">尚無分類</div>
          </div>
          <div class="gbtns">
            <button class="btn ghost ib" @click="addGroup"><Icon name="plus" :size="13" /> 新增</button>
            <button class="btn ghost ib" :disabled="!selected" @click="renameGroup"><Icon name="pencil" :size="13" /> 重命名</button>
            <button class="btn ghost ib" :disabled="!selected" @click="deleteGroup"><Icon name="trash" :size="13" /> 刪除</button>
            <button class="btn ghost ib" :disabled="!selected" @click="move(-1)"><Icon name="up" :size="13" /> 上移</button>
            <button class="btn ghost ib" :disabled="!selected" @click="move(1)"><Icon name="down" :size="13" /> 下移</button>
          </div>
        </div>

        <!-- middle: how the selected group is displayed -->
        <div class="mid">
          <div class="lab">顯示方式</div>
          <div v-if="!selected" class="none">先選一個分類</div>
          <div v-else class="vbox">
            <div class="gname" :title="selected">{{ selected }}</div>

            <div class="sub">點進角色後顯示的分頁</div>
            <label class="ck"><input type="checkbox" :checked="cur.videos"
                     @change="setView({ videos: $event.target.checked })" />
              <Icon name="film" :size="13" /> 影片</label>
            <label class="ck"><input type="checkbox" :checked="cur.profile"
                     @change="setView({ profile: $event.target.checked })" />
              <Icon name="profile" :size="13" /> 個人資料</label>
            <!-- No "you must keep one" warning: setView turns the other one
                 back on instead, so the state can never be reached. -->

            <div class="sub" :class="{ off: !cur.videos }">影片卡片比例</div>
            <select :value="cur.ratio" :disabled="!cur.videos"
                    @change="setView({ ratio: $event.target.value })">
              <option v-for="k in RATIO_KEYS" :key="k" :value="k">
                {{ RATIOS[k].label }}</option>
            </select>

            <label class="ck" :class="{ off: !cur.videos }">
              <input type="checkbox" :checked="cur.poster" :disabled="!cur.videos"
                     @change="setView({ poster: $event.target.checked })" />
              優先使用同名圖片</label>
            <div class="hint">{{ posterHint }}</div>

            <div class="sub" :class="{ off: !cur.profile }">個人資料照片</div>
            <label class="ck" :class="{ off: !cur.profile }">
              <input type="radio" :checked="!cur.albums" :disabled="!cur.profile"
                     @change="setView({ albums: false })" />
              全部平鋪</label>
            <label class="ck" :class="{ off: !cur.profile }">
              <input type="radio" :checked="cur.albums" :disabled="!cur.profile"
                     @change="setView({ albums: true })" />
              依子資料夾分成相簿</label>
            <div class="hint">{{ albumHint }}</div>

            <div class="sub">主頁卡片比例</div>
            <select :value="cur.home" @change="setView({ home: $event.target.value })">
              <option v-for="k in HOME_KEYS" :key="k" :value="k">
                {{ HOME_SHAPES[k].label }}</option>
            </select>
            <div class="hint">{{ homeHint }}</div>
          </div>
        </div>

        <!-- right: character assignment -->
        <div class="right">
          <div class="lab">角色指派</div>
          <input v-model="search" placeholder="搜尋角色…" />
          <div class="clist">
            <div v-for="c in filteredChars" :key="c" class="crow">
              <span class="cname" :title="c">{{ c }}</span>
              <select v-model="assign[c]">
                <option value="">未分類</option>
                <option v-for="g in order" :key="g" :value="g">{{ g }}</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <div class="foot">
        <button class="btn" :disabled="loading" @click="save">確定</button>
        <button class="btn ghost" @click="emit('close')">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.8);
  display: flex; align-items: center; justify-content: center; z-index: 60; }
/* 860 → 1020: the display-settings column is new, and taking its 210px out of
   the character list left the names truncating. */
.dlg { background: var(--panel); border-radius: 10px; width: min(1020px, 95vw);
  height: min(640px, 90vh); display: flex; flex-direction: column; }
.dhead { display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid #313148; font-weight: bold; }
.body { flex: 1; display: flex; gap: 14px; padding: 14px 16px; min-height: 0; }
.lab { font-size: 12px; font-weight: bold; margin-bottom: 8px; }
.left { width: 240px; display: flex; flex-direction: column; min-height: 0; }
.glist { flex: 1; overflow-y: auto; background: var(--card);
  border: 1px solid #404058; border-radius: 6px; padding: 4px; }
.gitem { padding: 7px 10px; border-radius: 5px; cursor: pointer; font-size: 13px;
  display: flex; justify-content: space-between; align-items: center; }
.gitem:hover { background: var(--hover); }
.gitem.on { background: var(--accent); color: #fff; }
.cnt { font-size: 11px; opacity: .7; }
.none { color: var(--dim); font-size: 12px; padding: 10px; text-align: center; }
.gbtns { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-top: 8px; }
.gbtns .btn { padding: 5px 6px; font-size: 11px; }
/* Fixed width like the group list, not flexible: this column holds a handful
   of short controls, so letting it share the stretch would take space from the
   character list, which is the one part that actually wants it. */
.mid { width: 210px; display: flex; flex-direction: column; min-height: 0;
  border-left: 1px solid #313148; border-right: 1px solid #313148;
  padding: 0 12px; }
.vbox { display: flex; flex-direction: column; gap: 7px; overflow-y: auto; }
.gname { font-size: 12px; color: var(--accent); font-weight: bold;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { font-size: 11px; color: var(--dim); margin-top: 6px; }
.ck { display: flex; align-items: center; gap: 6px; font-size: 12px;
  cursor: pointer; }
.ck input { margin: 0; }
/* Dimmed rather than hidden: a control that disappears when 影片 is unticked
   reads as a bug, and its value still matters if 影片 is turned back on. */
.sub.off, .ck.off { opacity: .45; }
.mid select { width: 100%; }
.hint { font-size: 10px; color: var(--dim); line-height: 1.5; }
.right { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.clist { flex: 1; overflow-y: auto; margin-top: 8px; background: var(--bg);
  border: 1px solid #404058; border-radius: 6px; padding: 4px; }
.crow { display: flex; align-items: center; gap: 10px; padding: 4px 8px; }
.cname { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
select { background: var(--card); color: var(--text); border: 1px solid #555;
  border-radius: 4px; padding: 4px 6px; font-size: 12px; width: 170px; }
.foot { display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 16px; border-top: 1px solid #313148; }
.msg { padding: 40px; text-align: center; color: var(--dim); }
.msg.err { color: var(--danger); }
</style>
