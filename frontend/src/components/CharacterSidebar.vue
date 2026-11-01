<script setup>
import { ref, onMounted, computed, nextTick, watch } from 'vue'
import { api, errText } from '../api'
import { store, can } from '../store'
import { charStats, loadCharStats } from '../charstats'
import { isOpen, toggleGroup, openGroup, setAllGroups, groupData, applyGroups,
         loadGroups } from '../groups'
import { viewForChar, pickTab } from '../groupview'
import { loadVideoIndex, searchVideos, fullPath, videoIndex } from '../videosearch'
import Icon from './Icon.vue'
import { t } from '../i18n'
import { ts } from '../serverstrings.js'

const chars = ref([])
// shared with the home grid — see charstats.js
const counts = computed(() => charStats.counts)
const countsReady = computed(() => charStats.ready)
// Membership now lives in ../groups so App.vue can read it too — it has to
// know the open character's group before it can decide which tabs to draw, and
// a second copy here would drift the moment one of them saved.
const groupsMap = computed(() => groupData.groups)
const order = computed(() => groupData.order)
const filter = ref('')
// section open/closed now lives in ../groups so the home grid agrees
const expandedMerge = ref({}) // merged entries expanded to show each drive

const emit = defineEmits(['manage-groups', 'merge', 'toast'])
const ctx = ref(null)      // { x, y, char } right-click menu
const showGroupSub = ref(false)

// The menu was pinned straight to the cursor, so right-clicking a character
// near the bottom of a long list pushed it off screen — and expanding 設定分類
// made it taller still. Measure once it is in the DOM and pull it back inside.
const menuEl = ref(null)
const menuPos = ref({ x: 0, y: 0 })

async function placeMenu() {
  await nextTick()
  const el = menuEl.value
  if (!el || !ctx.value) return
  const gap = 8
  const { width, height } = el.getBoundingClientRect()
  menuPos.value = {
    x: Math.max(gap, Math.min(ctx.value.x, window.innerWidth - width - gap)),
    y: Math.max(gap, Math.min(ctx.value.y, window.innerHeight - height - gap)),
  }
}

function openCtx(e, c) {
  if (!(store.user?.can_edit && store.editMode)) return
  e.preventDefault()
  showGroupSub.value = false
  ctx.value = { x: e.clientX, y: e.clientY, char: c }
  menuPos.value = { x: e.clientX, y: e.clientY }   // corrected on the next tick
  placeMenu()
}
function closeCtx() { ctx.value = null; showGroupSub.value = false }

// the submenu changes the height, so re-place after it opens or closes
watch(showGroupSub, placeMenu)

// which group a character currently belongs to ('' = 未分類)
function groupOf(name) {
  for (const [g, members] of Object.entries(groupsMap.value))
    if (members.includes(name)) return g
  return ''
}

async function assignGroup(target) {
  const c = ctx.value.char; closeCtx()
  const next = {}
  for (const g of order.value) {
    next[g] = (groupsMap.value[g] || []).filter(n => n !== c.name)
  }
  if (target) {
    if (!next[target]) next[target] = []
    next[target].push(c.name)
  }
  try {
    // No `view` in the body: this only moves a character, and the server keeps
    // the stored display settings when the field is absent.
    const d = await api('/api/groups', {
      method: 'POST',
      body: JSON.stringify({ groups: next, order: order.value }),
    })
    applyGroups(d)
    emit('toast', target ? t('已移至「{name}」', { name: target }) : t('已移至未分類'))
  } catch (e) { alert(t('設定分類失敗：') + errText(e)) }
}

async function reindexChar() {
  const c = ctx.value.char; closeCtx()
  if (!store.faceAvailable) { alert(store.faceReason || t('人臉辨識不可用')); return }
  if (!confirm(t('重新索引「{name}」的人臉？\n\n（只會處理尚未索引的影片）', { name: c.name }))) return
  try {
    const { job_id } = await api('/api/face/index', {
      method: 'POST', body: JSON.stringify({ person: c.name }),
    })
    emit('toast', t('開始索引「{name}」…', { name: c.name }))
    while (true) {
      await new Promise(r => setTimeout(r, 900))
      const j = await api(`/api/face/job/${job_id}`)
      if (j.progress?.file) emit('toast', t('索引中 {name} / {file}', { name: c.name, file: j.progress.file }))
      if (j.status === 'done') { emit('toast', t('索引完成，新增 {n} 部', { n: j.result.indexed })); break }
      if (j.status === 'error') { emit('toast', t('索引失敗：') + ts(j.error)); break }
    }
  } catch (e) { alert(t('索引失敗：') + errText(e)) }
}

async function copyPath() {
  const c = ctx.value.char; closeCtx()
  const text = c.merged ? c.paths.join('\n') : c.path
  try {
    await navigator.clipboard.writeText(text)
    emit('toast', t('已複製資料夾路徑'))
  } catch { prompt(t('資料夾路徑：'), text) }
}

async function renameChar() {
  const c = ctx.value.char; closeCtx()
  if (c.merged) { alert(t('合併項目請先整合，再重新命名')); return }
  const name = prompt(t('新名稱：'), c.name)
  if (!name || !name.trim() || name === c.name) return
  try {
    await api('/api/character/rename', {
      method: 'POST',
      body: JSON.stringify({ path: c.path, new_name: name.trim() }),
    })
    if (store.selectedChar?.path === c.path) store.selectedChar = null
    load()
  } catch (e) { alert(t('重新命名失敗：') + errText(e)) }
}

function mergeChar() {
  const c = ctx.value.char; closeCtx()
  emit('merge', c)
}

// The sidebar is for navigation, so it always stays alphabetical: a list that
// reorders itself breaks the muscle memory of "that person sits about here".
// Ranking by count/size/recency lives on the home page instead.
// Aliases come from person_info.json and are filled in automatically by the
// catalogue lookup, so typing "Yuko Hoshikawa" has to find 星川優子.
function aliasHit(c, q) {
  return (c.aliases || []).find(a => a.toLowerCase().includes(q)) || ''
}

// Tags were not searchable for no better reason than that they only reached
// this component recently -- /api/characters carries them now, so this costs
// nothing but the two lines.
//
// The length floor is the one real decision. Tags in this library are one to
// three characters (`M`, `IG`, `JAV`), so a single-character query would drag
// in every character carrying `JAV` merely because "jav" contains "a". Two is
// enough to make the match mean something; the catalogue-number search has the
// same kind of floor for the same kind of reason.
const TAG_MIN = 2

function tagHit(c, q) {
  if (q.length < TAG_MIN) return ''
  return (c.tags || []).find(t => t.toLowerCase().includes(q)) || ''
}

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const list = q
    ? chars.value.filter(c => c.name.toLowerCase().includes(q) || aliasHit(c, q)
                              || tagHit(c, q)
                              || vhits.value.byChar.has(c.name))
    : chars.value
  return [...list].sort((a, b) =>
    a.name.toLowerCase().localeCompare(b.name.toLowerCase()))
})

// only worth showing when the name itself did not match — otherwise the hit
// looks arbitrary ("why is 星川優子 here when I typed Riko?")
function matchedAlias(c) {
  const q = filter.value.trim().toLowerCase()
  if (!q || c.name.toLowerCase().includes(q)) return ''
  return aliasHit(c, q)
}

// Same rule again: say why this character is in the results, but only when it
// is not already obvious. An alias wins if both matched -- two explanations
// for one hit is noise, and the alias is the more specific of the two.
function matchedTag(c) {
  const q = filter.value.trim().toLowerCase()
  if (!q || c.name.toLowerCase().includes(q) || aliasHit(c, q)) return ''
  return tagHit(c, q)
}

const byName_ = (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase())

const sections = computed(() => {
  const byName = new Map(filtered.value.map(c => [c.name, c]))
  const used = new Set()
  const out = []
  for (const g of order.value) {
    // sort inside the group too — the stored membership order is arbitrary
    const members = (groupsMap.value[g] || [])
      .map(n => byName.get(n)).filter(Boolean).sort(byName_)
    members.forEach(m => used.add(m.name))
    if (members.length) out.push({ name: g, members })
  }
  const rest = filtered.value.filter(c => !used.has(c.name))
  if (rest.length) out.push({ name: '未分類', members: rest })
  return out
})

async function load() {
  try {
    const [c] = await Promise.all([api('/api/characters'), loadGroups()])
    chars.value = c.characters
    // a reload or remount while a character is open: the watch above ran
    // before there was a list to find it in
    reveal()
  } catch { /* ignore */ }
  // counts walk every folder → load after the sidebar is already usable.
  // A remount (settings saved, archive finished…) means the numbers changed.
  loadCharStats(charStats.ready)
  // ~0.2 MB gzipped; fetched after the sidebar is already usable, and again
  // on a remount because archiving or deleting will have changed it
  loadVideoIndex(videoIndex.ready)
}

function goHome() { store.selectedChar = null; store.view = 'home' }
function goInbox() { store.selectedChar = null; store.view = 'inbox' }
function select(c, drivePath = null) {
  // drivePath: pick one specific folder of a merged entry
  store.selectedChar = drivePath
    ? { ...c, path: drivePath, merged: false }
    : c
  store.view = 'char'
  // The global 預設分頁 setting knows nothing about groups, so it can name a
  // tab this character's group hides. Land on one that is actually there.
  store.activeTab = pickTab(store.defaultTab,
                            viewForChar(c.name, groupData.groups, groupData.view))
}
function toggle(g) { toggleGroup(g) }

/** Open the character a matching video belongs to, and point at that file. */
function openHit(row, e) {
  e.stopPropagation()
  const path = fullPath(row)
  if (row[3]) {                       // inbox file: no character to open
    store.selectedChar = null
    store.view = 'inbox'
  } else {
    const c = chars.value.find(x => x.name === row[1])
    if (!c) return
    select(c)
    // A video hit has to open the video tab — unless the group hides it, in
    // which case there is nothing to point at and 個人資料 is where we land.
    store.activeTab = pickTab('videos',
                              viewForChar(c.name, groupData.groups, groupData.view))
  }
  // VideoGrid watches this, scrolls the row into view and flashes it
  store.focusFile = path
}

// Sections default to closed, so a search would otherwise return headers with
// counts and nothing under them — which reads as "search is broken".
const searching = computed(() => !!filter.value.trim())

// Video-name hits for the current query. A character can now appear because
// one of its *files* matched, so the row shows which file — the same reason
// an alias hit shows the alias: an unexplained result looks arbitrary.
const vhits = computed(() => searchVideos(filter.value))
function videoHit(name) { return vhits.value.byChar.get(name) || null }
function shown(name) { return searching.value || isOpen(name) }

const allOpen = computed(() =>
  sections.value.length > 0 && sections.value.every(s => isOpen(s.name)))
function toggleAll() {
  setAllGroups(sections.value.map(s => s.name), !allOpen.value)
}

// With everything closed by default, opening a character from the home grid
// would leave the sidebar showing no sign of where you are. Reveal it.
//
// Opening the group was not enough: 未分類 holds hundreds, so the highlighted
// row usually sat below the fold and the sidebar looked as if it had not
// followed. Scroll it to the middle — unless it is already in view, so a
// click in the sidebar itself never makes the list jump. A row the search
// box has filtered out is left alone: the query is the user's, not ours to
// clear. On a phone the drawer is only translated off-screen, so this lands
// while it is shut and it opens already in place.
const listEl = ref(null)

// The list already filters as you type; the button is for when that is not
// obvious, and on a phone it puts the keyboard away so the results show.
const searchEl = ref(null)
function runSearch() {
  if (filter.value.trim()) searchEl.value?.blur()
  else searchEl.value?.focus()
}
function clearSearch() {
  filter.value = ''
  searchEl.value?.focus()
}

async function reveal() {
  const name = store.selectedChar?.name
  if (!name) return
  const sec = sections.value.find(s => s.members.some(m => m.name === name))
  if (sec) openGroup(sec.name)
  await nextTick()
  const list = listEl.value
  const row = list?.querySelector('.item.child.active')
  if (!row) return
  const lr = list.getBoundingClientRect(), rr = row.getBoundingClientRect()
  if (rr.top >= lr.top && rr.bottom <= lr.bottom) return
  list.scrollTop += rr.top - lr.top - (lr.height - rr.height) / 2
}
watch(() => store.selectedChar?.path, reveal, { immediate: true })
function toggleMerge(name, e) { e.stopPropagation(); expandedMerge.value[name] = !expandedMerge.value[name] }
function cnt(name) { return countsReady.value ? (counts.value[name] ?? 0) : null }
function driveOf(p) { return (p.match(/^[A-Za-z]:/) || [p.slice(0, 12)])[0] }

defineExpose({ reload: load })
onMounted(load)
</script>

<template>
  <aside class="sidebar">
    <div class="sbox">
      <input ref="searchEl" v-model="filter" class="search"
             :placeholder="t('搜尋角色、標籤或影片…')"
             @keydown.enter="runSearch" @keydown.esc="clearSearch" />
      <button v-if="filter" class="sbtn sclear" :title="t('清除')" :aria-label="t('清除')"
              @click="clearSearch"><Icon name="close" :size="14" /></button>
      <button class="sbtn sgo" :title="t('搜尋')" :aria-label="t('搜尋')"
              @click="runSearch"><Icon name="search" :size="14" /></button>
    </div>

    <!-- outside .list, so scrolling the characters leaves these in place -->
    <div class="nav">
      <div class="navrow">
        <div class="item ib" :class="{ active: store.view === 'home' }" @click="goHome">
          <Icon name="home" /> {{ t('主頁') }}
        </div>
        <div v-if="store.hasInbox" class="item ib"
             :class="{ active: store.view === 'inbox' }" @click="goInbox">
          <Icon name="inbox" /> {{ t('待分類') }}
        </div>
        <span class="spacer"></span>
        <button class="alltog" :title="allOpen ? t('全部收合') : t('全部展開')"
                @click="toggleAll">
          <Icon :name="allOpen ? 'chevronDown' : 'chevronRight'" :size="12" />
          {{ allOpen ? t('全部收合') : t('全部展開') }}
        </button>
      </div>
      <div class="sep"></div>
    </div>

    <div ref="listEl" class="list">
      <template v-for="s in sections" :key="s.name">
        <div class="ghdr" @click="toggle(s.name)">
          <Icon :name="shown(s.name) ? 'chevronDown' : 'chevronRight'" :size="13" />
          <!-- 未分類 stays the key (open/closed state is stored by it); only
               its label is translated. A group the user named is theirs. -->
          {{ s.name === '未分類' ? t('未分類') : s.name }} <span class="gcnt">({{ s.members.length }})</span>
        </div>
        <template v-if="shown(s.name)">
          <template v-for="c in s.members" :key="c.path">
            <!-- merged entry: same folder name on several drives -->
            <div v-if="c.merged" class="item child merged"
                 :class="{ active: store.view === 'char' && store.selectedChar?.name === c.name }"
                 @click="select(c)" @contextmenu="e => openCtx(e, c)">
              <span class="mtoggle" @click="toggleMerge(c.name, $event)">
                <Icon :name="expandedMerge[c.name] ? 'chevronDown' : 'chevronRight'" :size="12" />
              </span>
              <Icon name="warn" :size="13" /> {{ c.name }}
              <span class="drv">({{ c.drives }})</span>
              <span v-if="cnt(c.name) !== null" class="n">({{ cnt(c.name) }})</span>
              <span v-if="matchedAlias(c)" class="alias">· {{ matchedAlias(c) }}</span>
              <span v-else-if="matchedTag(c)" class="tagm">· {{ matchedTag(c) }}</span>
            </div>
            <template v-if="c.merged && expandedMerge[c.name]">
              <div v-for="p in c.paths" :key="p" class="item grandchild"
                   @click.stop="select(c, p)">{{ driveOf(p) }} {{ p }}</div>
            </template>

            <!-- normal character -->
            <div v-else class="item child"
                 :class="{ active: store.view === 'char' && store.selectedChar?.path === c.path }"
                 @click="select(c)" @contextmenu="e => openCtx(e, c)">
              {{ c.name }}
              <span v-if="cnt(c.name) !== null" class="n">({{ cnt(c.name) }})</span>
              <span v-if="matchedAlias(c)" class="alias">· {{ matchedAlias(c) }}</span>
              <span v-else-if="matchedTag(c)" class="tagm">· {{ matchedTag(c) }}</span>
            </div>
            <!-- why this character is here: the files that matched.
                 Without it a search for a catalogue number just produces a
                 list of names with no visible reason. -->
            <template v-if="videoHit(c.name)">
              <div v-for="row in videoHit(c.name).shown" :key="row[0]"
                   class="item vhit" :title="fullPath(row)"
                   @click="e => openHit(row, e)">
                <Icon name="film" :size="11" /> {{ row[0] }}
              </div>
              <div v-if="videoHit(c.name).count > videoHit(c.name).shown.length"
                   class="item vmore" @click.stop="select(c)">
                {{ t('還有 {n} 個相符', { n: videoHit(c.name).count - videoHit(c.name).shown.length }) }}
              </div>
            </template>
          </template>
        </template>
      </template>

      <!-- inbox hits have no character to sit under -->
      <template v-if="searching && vhits.inbox.length">
        <div class="ghdr plain"><Icon name="inbox" :size="13" /> {{ t('待分類') }}</div>
        <div v-for="row in vhits.inbox" :key="row[0]" class="item vhit"
             :title="fullPath(row)" @click="e => openHit(row, e)">
          <Icon name="film" :size="11" /> {{ row[0] }}
        </div>
      </template>

      <div v-if="searching && !sections.length && !vhits.inbox.length"
           class="nores">{{ t('找不到符合的角色或影片') }}</div>
    </div>

    <!-- right-click menu (edit mode only) -->
    <div v-if="ctx" class="ctxmask" @click="closeCtx" @contextmenu.prevent="closeCtx">
      <div ref="menuEl" class="ctxmenu"
           :style="{ left: menuPos.x + 'px', top: menuPos.y + 'px' }" @click.stop>
        <div class="ctxname">{{ ctx.char.name }}</div>
        <div class="ctxitem ib" @click="copyPath"><Icon name="copy" :size="14" /> {{ t('複製資料夾路徑') }}</div>
        <div v-if="can('person')" class="ctxitem ib" :class="{ dis: !store.faceAvailable }"
             @click="reindexChar">
          <Icon name="refresh" :size="14" /> {{ t('重新索引此人') }}
        </div>
        <div v-if="can('rename_merge')" class="ctxitem ib" @click="renameChar"><Icon name="pencil" :size="14" /> {{ t('重新命名') }}</div>
        <div v-if="ctx.char.merged && can('rename_merge')" class="ctxitem ib" @click="mergeChar"><Icon name="merge" :size="14" /> {{ t('整合資料夾…') }}</div>

        <div v-if="can('groups')" class="ctxsep"></div>
        <div v-if="can('groups')" class="ctxitem sub" @click="showGroupSub = !showGroupSub">
          <span class="ib"><Icon name="tags" :size="14" /> {{ t('設定分類') }}</span>
          <Icon :name="showGroupSub ? 'chevronDown' : 'chevronRight'" :size="12" class="chev" />
        </div>
        <template v-if="showGroupSub">
          <div class="ctxitem indent" @click="assignGroup('')">
            <span class="tick"><Icon v-if="groupOf(ctx.char.name) === ''" name="check" :size="12" /></span> {{ t('未分類') }}
          </div>
          <div v-for="g in order" :key="g" class="ctxitem indent" @click="assignGroup(g)">
            <span class="tick"><Icon v-if="groupOf(ctx.char.name) === g" name="check" :size="12" /></span> {{ g }}
          </div>
          <div class="ctxitem indent" @click="closeCtx(); emit('manage-groups')">
            <span class="ib"><Icon name="settings" :size="13" /> {{ t('管理分類…') }}</span>
          </div>
        </template>
      </div>
    </div>

    <div class="foot">
      <span>{{ t('{n} 個角色', { n: chars.length }) }}</span>
      <button v-if="store.editMode && can('groups')"
              class="mg no-mobile" @click="emit('manage-groups')">{{ t('管理分類') }}</button>
    </div>
  </aside>
</template>

<style scoped>
.vhit { font-size: 11px; color: var(--dim); padding-left: 26px;
  display: flex; align-items: center; gap: 4px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.vhit:hover { color: var(--accent); }
.vmore { font-size: 10px; color: var(--dim); padding-left: 26px; font-style: italic; }
.ghdr.plain { cursor: default; }
.nores { color: var(--dim); font-size: 12px; padding: 10px 12px; }

.sidebar { width: 260px; flex-shrink: 0; background: var(--panel);
  border-right: 1px solid #313148; display: flex; flex-direction: column;
  height: calc(100vh - 53px - 26px);
  height: calc(100dvh - 53px - 26px); }
.alltog { background: none; border: none; color: var(--dim); font-size: 11px;
  cursor: pointer; display: inline-flex; align-items: center; gap: 2px;
  padding: 0 6px; flex-shrink: 0; white-space: nowrap; }
.alltog:hover { color: var(--accent); }
.sbox { position: relative; margin: 12px 10px 8px; }
.search { width: 100%; padding-right: 54px; }
.sbtn { position: absolute; top: 50%; transform: translateY(-50%);
  width: 24px; height: 24px; padding: 0; border: none; border-radius: 4px;
  background: none; color: var(--dim); cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; }
.sbtn:hover { color: var(--accent); background: var(--hover); }
.sgo { right: 4px; }
.sclear { right: 28px; }
.nav { padding: 0 6px; flex-shrink: 0; }
.navrow { display: flex; align-items: center; gap: 2px; }
.navrow .item { flex-shrink: 0; }
.navrow .spacer { flex: 1; }
.alias { color: var(--accent); font-size: 10px; margin-left: 4px; }
/* Same job as .alias — "this is why the row is here" — but a different colour,
   so a hit on a tag is not read as an alias the character does not have. */
.tagm { color: #a6e3a1; font-size: 10px; margin-left: 4px; }
.list { overflow-y: auto; flex: 1; padding: 0 6px 10px;
  /* own compositing layer: hover repaints the whole layer, so no stale
     hairline seams are left between rows on fractional-DPI displays */
  transform: translateZ(0); }
.item { padding: 6px 10px; font-size: 13px; line-height: 16px; border-radius: 5px;
  cursor: pointer; margin: 1px 0;   /* rows never share an edge → no seam */
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text); }
.item:hover { background: var(--hover); }
.item.active { background: var(--accent); color: #fff; }
.item.child { padding-left: 20px; }
.item.merged { color: #f9e2af; display: flex; align-items: center; gap: 4px; }
.item.grandchild { padding-left: 38px; font-size: 11px; color: var(--dim); }
.mtoggle { display: inline-block; width: 12px; }
.drv { font-size: 10px; opacity: .8; }
.n { color: var(--dim); font-size: 11px; }
.item.active .n { color: rgba(255,255,255,.75); }
.sep { height: 1px; background: #313148; margin: 6px 8px; }
.ghdr { color: var(--accent); font-size: 12px; font-weight: bold;
  padding: 8px 8px 4px; cursor: pointer; user-select: none;
  display: flex; align-items: center; gap: 5px; }
.ghdr:hover { color: #9090ff; }
.arrow { display: inline-block; width: 12px; }
.gcnt { color: var(--dim); font-weight: normal; }
.foot { display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-top: 1px solid #313148; font-size: 11px; color: var(--dim); }
.mg { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 10px; cursor: pointer; }
.mg:hover { background: var(--hover); border-color: var(--accent); }
.ctxmask { position: fixed; inset: 0; z-index: 70; }
.ctxmenu { position: fixed; background: var(--panel); border: 1px solid #444;
  border-radius: 6px; padding: 4px; min-width: 160px; box-shadow: 0 4px 16px rgba(0,0,0,.5);
  /* with enough groups the menu can still outgrow the screen — scroll it
     rather than letting the last entries fall off the bottom */
  max-height: calc(100vh - 16px); overflow-y: auto; }
.ctxname { font-size: 10px; color: var(--dim); padding: 4px 10px 6px;
  border-bottom: 1px solid #313148; margin-bottom: 4px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ctxitem { padding: 7px 10px; font-size: 12px; border-radius: 4px; cursor: pointer;
  white-space: nowrap; }
.ctxitem:hover { background: var(--accent); color: #fff; }
.ctxitem.dis { color: var(--dim); }
.ctxitem.sub { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.ctxitem.indent { display: flex; align-items: center; gap: 4px; }
.ctxitem.indent { padding-left: 22px; }
.chev { color: var(--dim); font-size: 10px; }
.ctxitem:hover .chev { color: #fff; }
.tick { display: inline-block; width: 12px; color: var(--success); }
.ctxitem:hover .tick { color: #fff; }
.ctxsep { height: 1px; background: #313148; margin: 4px 0; }

/* 24px icons are a mouse affordance; a thumb needs more */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .search { padding-right: 76px; }
  .sbtn { width: 34px; height: 34px; }
  .sgo { right: 2px; }
  .sclear { right: 38px; }
  .alltog { min-height: 36px; padding: 0 10px; }
}
</style>
