<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api, imageUrl, imageThumbUrl } from '../api'
import { store } from '../store'
import { charStats, loadCharStats } from '../charstats'
import { isOpen, toggleGroup, setAllGroups, groupData, loadGroups } from '../groups'
import { viewForChar, pickTab, viewOf, HOME_SHAPES, homeStyle, cardImage }
  from '../groupview'
import { avatarImgStyle } from '../avatar'
import { sizeBatcher } from '../imgsizes.js'
import Icon from './Icon.vue'

const chars = ref([])
// shared with the sidebar and App.vue — see groups.js
const groupsMap = computed(() => groupData.groups)
const order = computed(() => groupData.order)
const stats = computed(() => charStats.stats)   // name → { count, bytes, newest }
const statsReady = computed(() => charStats.ready)
const loading = ref(true)
const natural = ref({})        // path → { w, h }, filled as each image loads
// Name is the default because it is the only sort that needs no extra data:
// count/bytes/newest all come from /api/characters/counts, which walks every
// folder in the background. Sorting by those on first paint made the whole
// grid re-shuffle a second or two after it appeared.
const LS_SORT = 'vam.home.sort'
const NEEDS_STATS = new Set(['newest', 'count', 'bytes'])
const sortBy = ref(localStorage.getItem(LS_SORT) || 'name')

// Section open/closed is shared with the sidebar — see groups.js. Closed by
// default, which matters most here: 465 of 619 characters are 未分類, so the
// grid used to render ~620 avatar cards on every visit.
const allOpen = computed(() =>
  sections.value.length > 0 && sections.value.every(s => isOpen(s.name)))
function toggleAll() {
  setAllGroups(sections.value.map(s => s.name), !allOpen.value)
}

// what we can actually sort by right now
const effectiveSort = computed(() =>
  NEEDS_STATS.has(sortBy.value) && !statsReady.value ? 'name' : sortBy.value)
const pending = computed(() => effectiveSort.value !== sortBy.value)

function setSort(s) {
  sortBy.value = s
  localStorage.setItem(LS_SORT, s)
}

// Batched, and silent for sizes already known — each update re-renders the
// whole grid, and doing it once per image was ~46,000 card renders when a
// phone opened JAV's 228 cards (see imgsizes.js).
const reportSize = sizeBatcher(() => natural.value,
  batch => { natural.value = { ...natural.value, ...batch } })
function onImgLoad(e, c) {
  reportSize(c.path, e.target.naturalWidth, e.target.naturalHeight)
}

// same framing the profile header uses, so the two always agree.
// `boxAr` matters now that a section can be 16:9 / 4:5 / 9:16: the maths used
// to assume a square box, so a rectangular one sized the picture wrongly.
// A folder cover has no stored framing, so it is plain centred `cover`.
function styleFor(c, shape) {
  const pic = cardImage(c, shape)
  if (!pic || !pic.framed) return {}
  const n = natural.value[c.path]
  const s = HOME_SHAPES[shape] || HOME_SHAPES.circle
  const [a, b] = s.css.split('/').map(Number)
  return avatarImgStyle(n?.w, n?.h, c.avatar_pos, a / b)
}

// Display settings for a section. 未分類 is derived rather than stored, so it
// is not configurable and lands on the defaults — a round tile.
function shapeOf(name) { return viewOf(name, groupData.view).home }
function picOf(c, shape) { return cardImage(c, shape) }

function stat(name) { return stats.value[name] || null }
function fmtSize(b) {
  if (!b) return ''
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(1) + ' GB'
  return (b / (1 << 20)).toFixed(0) + ' MB'
}
function fmtAge(ts) {
  if (!ts) return ''
  const days = (Date.now() / 1000 - ts) / 86400
  if (days < 1) return '今天'
  if (days < 30) return `${Math.floor(days)} 天前`
  if (days < 365) return `${Math.floor(days / 30)} 個月前`
  return `${Math.floor(days / 365)} 年前`
}

function sortChars(list) {
  const s = [...list]
  const g = n => stats.value[n] || { count: -1, bytes: -1, newest: 0 }
  const by = effectiveSort.value
  if (by === 'count')       s.sort((a, b) => g(b.name).count - g(a.name).count)
  else if (by === 'bytes')  s.sort((a, b) => g(b.name).bytes - g(a.name).bytes)
  else if (by === 'newest') s.sort((a, b) => g(b.name).newest - g(a.name).newest)
  else s.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()))
  return s
}

// ── tag filter ─────────────────────────────────────────────────────────────
// Set by clicking a tag on a profile. Applied before grouping so the sections
// collapse to just the ones with a match, rather than leaving a page of empty
// headings.
const tagged = computed(() => {
  const t = store.tagFilter
  if (!t) return chars.value
  return chars.value.filter(c => (c.tags || []).includes(t))
})

// Groups are collapsed by default (465 of 619 are 未分類), which would hide
// the very result the filter just found. While a filter is on, everything is
// open: you asked a question and the answer should be on screen.
const filtering = computed(() => !!store.tagFilter)

function clearTag() { store.tagFilter = null }

// same grouping as the sidebar; ungrouped characters fall into 未分類
const sections = computed(() => {
  const sorted = sortChars(tagged.value)
  const byName = new Map(sorted.map(c => [c.name, c]))
  const used = new Set()
  const out = []
  for (const g of order.value) {
    const members = sortChars((groupsMap.value[g] || [])
      .map(n => byName.get(n)).filter(Boolean))
    members.forEach(m => used.add(m.name))
    if (members.length) out.push({ name: g, members })
  }
  const rest = sorted.filter(c => !used.has(c.name))
  if (rest.length) out.push({ name: '未分類', members: rest })
  return out
})

async function load() {
  try {
    const [c] = await Promise.all([api('/api/characters'), loadGroups()])
    chars.value = c.characters
  } catch { /* ignore */ } finally {
    loading.value = false
  }
  // shared with the sidebar, so returning to the home grid costs nothing
  loadCharStats()
}

function open(c) {
  store.selectedChar = c
  store.view = 'char'                 // ← this was missing: clicks did nothing
  // Same correction as the sidebar's select(): the global 預設分頁 can name a
  // tab this character's group hides.
  store.activeTab = pickTab(store.defaultTab,
                            viewForChar(c.name, groupData.groups, groupData.view))
}

onMounted(load)
// avatars set from 角色查詢 → 個資 while this grid sat behind the dialog
watch(() => store.charsVersion, load)
</script>

<template>
  <div class="home">
    <div class="htitle">
      <span class="ib"><Icon name="home" :size="17" /> 主頁 · {{ chars.length }} 位角色</span>
      <!-- A filter you cannot see is a bug report waiting to happen: without
           this the grid just looks like most of the library vanished. -->
      <button v-if="store.tagFilter" class="tagfilter ib" @click="clearTag">
        <Icon name="tags" :size="12" /> {{ store.tagFilter }}
        <span class="fcount">{{ tagged.length }} 位</span>
        <Icon name="close" :size="12" />
      </button>
      <span class="spacer"></span>
      <!-- Label and buttons wrap as one unit; see the phone rules below. -->
      <span class="tgroup">
      <button class="tbtn" @click="toggleAll">
        {{ allOpen ? '全部收合' : '全部展開' }}
      </button>
      <span class="slab">排序</span>
      <button class="tbtn" :class="{ on: sortBy === 'newest', wait: sortBy === 'newest' && pending }"
              @click="setSort('newest')">最近新增</button>
      <button class="tbtn" :class="{ on: sortBy === 'count', wait: sortBy === 'count' && pending }"
              @click="setSort('count')">數量</button>
      <button class="tbtn" :class="{ on: sortBy === 'bytes', wait: sortBy === 'bytes' && pending }"
              @click="setSort('bytes')">容量</button>
      <button class="tbtn" :class="{ on: sortBy === 'name' }" @click="setSort('name')">名稱</button>
      <span v-if="pending" class="waitlab ib"><Icon name="spinner" :size="11" class="spin" /> 計算中</span>
      </span>
    </div>

    <div v-if="loading" class="msg">載入中…</div>
    <div v-else-if="store.tagFilter && !tagged.length" class="msg">
      沒有角色標了「{{ store.tagFilter }}」
    </div>
    <template v-else v-for="s in sections" :key="s.name">
      <div class="ghdr" @click="toggleGroup(s.name)">
        <Icon :name="filtering || isOpen(s.name) ? 'chevronDown' : 'chevronRight'" :size="14" />
        {{ s.name }} <span class="gcnt">({{ s.members.length }})</span>
      </div>
      <div v-if="filtering || isOpen(s.name)" class="grid"
           :style="homeStyle(shapeOf(s.name))">
        <div v-for="c in s.members" :key="c.path" class="card" @click="open(c)">
          <div class="ava" :class="{ rect: shapeOf(s.name) !== 'circle' }">
            <!-- imageThumbUrl, not imageUrl: these were full-size originals,
                 and the largest avatar in the library is 19.1 MB for a tile
                 130px wide. The framing maths only uses the aspect ratio,
                 which the downscale preserves. -->
            <img v-if="picOf(c, shapeOf(s.name))"
                 :src="imageThumbUrl(picOf(c, shapeOf(s.name)).src)" loading="lazy"
                 :class="{ plain: !picOf(c, shapeOf(s.name)).framed }"
                 :style="styleFor(c, shapeOf(s.name))" @load="e => onImgLoad(e, c)" />
            <Icon v-else name="user" :size="34" class="ph" />
          </div>
          <div class="nm" :title="c.name">{{ c.name }}</div>
          <!-- the caption reports whatever you sorted by; sorting by name has
               nothing to report, so it stays out of the way -->
          <div v-if="sortBy !== 'name' && stat(c.name)" class="sub">
            <template v-if="sortBy === 'bytes'">{{ fmtSize(stat(c.name).bytes) }}</template>
            <template v-else-if="sortBy === 'newest'">{{ fmtAge(stat(c.name).newest) }}</template>
            <template v-else>{{ stat(c.name).count }} 部</template>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.home { padding: 20px; }
.htitle { display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 14px; font-weight: bold; color: var(--text); margin-bottom: 16px; }
.spacer { flex: 1; }
.slab { color: var(--dim); font-size: 11px; font-weight: normal; margin-left: 8px; }
/* Sits next to the title rather than with the sort buttons: it is not another
   view option, it is the reason the grid is showing what it is showing. */
.tagfilter { background: var(--accent); border: none; border-radius: 12px;
  color: #fff; font: inherit; font-size: 11px; font-weight: bold;
  padding: 3px 10px; margin-left: 8px; cursor: pointer; gap: 5px; }
.tagfilter:hover { filter: brightness(1.15); }
.tagfilter .fcount { font-weight: normal; opacity: .8; }
.tbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; font-weight: normal; padding: 3px 10px;
  cursor: pointer; }
.tbtn:hover { border-color: var(--accent); }
.tbtn.on { background: var(--accent); border-color: var(--accent); color: #fff; }
/* picked, but the numbers behind it have not arrived yet */
.tbtn.wait { background: transparent; border-color: var(--accent); color: var(--accent); }
.waitlab { color: var(--dim); font-size: 10px; font-weight: normal; }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.msg { color: var(--dim); padding: 40px; text-align: center; }
.ghdr { color: var(--accent); font-size: 13px; font-weight: bold;
  margin: 20px 0 10px; padding-bottom: 4px; border-bottom: 1px solid #313148;
  display: flex; align-items: center; gap: 5px; cursor: pointer;
  user-select: none; }
.ghdr:hover { color: var(--text); }
.gcnt { color: var(--dim); font-weight: normal; font-size: 11px; }
.grid {
  display: grid;
  /* shape and column width come from the group's setting — see groupview.js.
     They move together: a 16:9 picture at the round tile's 130px is 73px tall,
     which reads as a strip rather than a card. */
  grid-template-columns: repeat(auto-fill, minmax(var(--home-min, 130px), 1fr));
  gap: 16px;
}
.card { cursor: pointer; text-align: center; }
.ava {
  position: relative;
  width: 100%; aspect-ratio: var(--home-ratio, 1);
  border-radius: var(--home-radius, 50%); overflow: hidden;
  background: var(--card); border: 3px solid #404058;
  display: flex; align-items: center; justify-content: center;
  transition: border-color .12s;
}
.card:hover .ava { border-color: var(--accent); }
/* size/offset come from avatarImgStyle() — identical to the profile header */
.ava img { display: block; }
/* A folder cover has no stored focal point, so it is not laid out by
   avatarImgStyle() — plain centred cover instead. */
.ava img.plain { position: absolute; inset: 0;
  width: 100%; height: 100%; object-fit: cover; }
/* A rectangle is a card, not a portrait: the 3px ring reads as a frame at
   130px round and as a border at 210px wide. */
.ava.rect { border-width: 2px; }
.ava .ph { font-size: 40px; color: var(--dim); }
.nm { margin-top: 8px; font-size: 12px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.sub { color: var(--dim); font-size: 10px; margin-top: 2px; }

.tgroup { display: inline-flex; align-items: center; gap: 6px; flex-wrap: nowrap; }
.tbtn, .waitlab { white-space: nowrap; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  /* The title gets the first line, the buttons the second (scrolling
     sideways if they still do not fit) — never split across both. */
  .htitle .spacer { display: none; }
  .tgroup { flex-basis: 100%; overflow-x: auto; scrollbar-width: none; }
  .tgroup::-webkit-scrollbar { display: none; }
}
</style>
