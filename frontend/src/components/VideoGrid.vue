<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, watch, computed } from 'vue'
import { api, thumbUrl, streamUrl, subtitleUrl, errText } from '../api'
import { FILTERS, filterVideos, isWikiMode, postWatched, postFav, markIcons,
         relTag, favGuard, favGuardMessage }
  from '../videoflags'
import { sectionName } from '../wikisections'
import { gridStyle } from '../groupview'
import { titleTargets } from '../filetitle'
import WikiMissingDialog from './WikiMissingDialog.vue'
import { store, can, setStatus } from '../store'
import { releaseFile } from '../player'
import VideoPlayer from './VideoPlayer.vue'
import SheetViewer from './SheetViewer.vue'
import { naturalCompare } from '../photosort'
import ClipDialog from './ClipDialog.vue'
import Icon from './Icon.vue'
import { screen } from '../mobile'
import { loadVideoIndex } from '../videosearch'
import { relVerb, relNote } from '../relinfo.js'

const props = defineProps({
  root: { type: String, default: null },    // character folder path
  scope: { type: String, default: null },   // 'inbox' = 待分類資料夾
  merged: { type: String, default: null },  // merge-group base name
  // Card shape and picture source, from the group's display settings. Both
  // only reach here for a character folder; the inbox has no group.
  ratio: { type: String, default: '16:9' },
  poster: { type: Boolean, default: false },
})

// The grid is CSS-variable driven rather than class-per-ratio: the column
// minimum has to change with the shape, or 4:5 cards at the 16:9 grid's 350px
// floor come out 438px tall.
const gvars = computed(() => gridStyle(props.ratio))
// Same URL builder, one extra flag. Off by default everywhere else: a poster
// comes from a *different file*, so serving it from the compare view or a
// duplicate report would let two different videos show the same picture.
// the mtime makes a file replaced in place get a new URL — see `thumbUrl`.
// With covers on, the cover's mtime (`pm`) counts too: a cover downloaded
// later (角色查詢 → 片名) would otherwise wait out the frame grab's month in
// the browser cache. The user's rule for the picture: by the card's shape —
// a 16:9 card shows `-fanart` (the sleeve), a portrait one `-poster` (the front).
const thumbFor = (v) => thumbUrl(v.path, 0.1, 640, props.poster,
  props.poster ? Math.max(v.mtime, v.pm || 0) : v.mtime, props.ratio !== '16:9')

const videos = ref([])
// Videos filed under *other* characters that this one co-stars in (共演).
// Their own list, not a subset of `videos`: none of them are in this folder.
// See backend/app/core/costar.py.
const costarVideos = ref([])
// Mixed into the listing by default (the user's choice, 2026-10-23: opening a
// co-star should show everything they are in, not hide part of it behind a
// button). `ownerMode` narrows it: all / own folder only / co-star only. It is
// its own small switch rather than a FILTERS mode so it combines with 未看過
// and 我的最愛, and it is not remembered: it is about this character.
const ownerMode = ref('all')
// Worded so they do not read as a second 「全部」 next to the FILTERS row.
const OWNER_MODES = [['all', '含共演'], ['own', '只看自己'], ['costar', '只看共演']]
const ownerBase = computed(() =>
  ownerMode.value === 'own' ? videos.value
    : ownerMode.value === 'costar' ? costarVideos.value
      : [...videos.value, ...costarVideos.value])
const loading = ref(true)
const error = ref('')
const playing = ref(null)   // currently-open video

// The extension is the same on almost every file and says nothing; the film
// title, when we have one, is what actually distinguishes a bare catalogue
// number like SDMU-066.mp4.
function stem(name) { return String(name).replace(/\.[^.]+$/, '') }

// ── contact sheet ──────────────────────────────────────────────────────────
// Opened per video from the card, never automatically: each sheet costs about
// 2.5 s of ffmpeg, so building them for a whole folder on sight would be far
// worse than the problem it solves.
const sheetFor = ref(null)
function openSheet(v, e) {
  e.stopPropagation()
  sheetFor.value = v
}
// A `.tmp.ts` is a live recording still being written: the recorder renames
// it when it stops, so a sheet built now is cached under a name that is about
// to vanish, covers only what was recorded so far, and is slow to make (25 s
// for one sheet of a 5.3 GB recording, the disk busy with the recorder). The
// 74 finished .ts in the library are fine (0.4-5.5 s), so only these go.
const canSheet = v => !/\.tmp\.ts$/i.test(v.path)

// ── clipping ───────────────────────────────────────────────────────────────
const clipFor = ref(null)
const clipInitial = ref([])
function openClip(v, e) {
  e.stopPropagation()
  clipInitial.value = []
  clipFor.value = v
}

// Stage one happens on the contact sheet (coarse, one cell per interval) and
// stage two here (frame level). The sheet closes rather than staying behind
// the dialog: the choosing is done, and two stacked modals over the same video
// is a good way to lose track of which one the keyboard is talking to.
function fromSheet(segments) {
  const v = sheetFor.value
  if (!v || !segments?.length) return
  sheetFor.value = null
  clipInitial.value = segments
  clipFor.value = v
}

// The clip lands next to its source, so it belongs in this list straight away
// — reloading the whole folder to see it would throw away the scroll position
// mid-tidy, which is exactly when this feature gets used.
function onClipCreated(item) {
  if (videos.value.some(v => v.path === item.path)) return
  videos.value = [...videos.value, { ...item, title: clipFor.value?.title || '' }]
}

function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(1) + ' MB'
}

// ── sorting ────────────────────────────────────────────────────────────────
// Only fields /api/videos already returns: duration would need one ffprobe per
// file, which is exactly the cost we avoid everywhere else.
const LS_SORT = 'vam.videos.sort'
const NATURAL_DIR = { newest: 'desc', name: 'asc', size: 'desc',
                     released: 'desc' }
// 最近新增 is the file's mtime — when this copy arrived. 發行日 is when the
// film came out, which for an archive of old releases is a different order
// entirely. Only offered where anything has one.
const ALL_SORTS = [['newest', '最近新增'], ['name', '名稱'], ['size', '大小'],
                   ['released', '發行日']]
const saved = (() => {
  try { return JSON.parse(localStorage.getItem(LS_SORT) || '{}') } catch { return {} }
})()
const sortBy = ref(saved.by || 'newest')     // newest | name | size
const sortDir = ref(saved.dir || 'desc')

function setSort(by) {
  // clicking the active field flips direction; a new field starts natural
  if (sortBy.value === by) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortBy.value = by; sortDir.value = NATURAL_DIR[by] }
  localStorage.setItem(LS_SORT, JSON.stringify({ by: sortBy.value, dir: sortDir.value }))
}

// ── wiki summary ───────────────────────────────────────────────────────────
// Present only for characters whose seesaawiki page has been imported. The
// count beside "n 部影片" is deliberately behind an icon rather than in the
// line itself, because the numbers do not add up and cannot be made to:
// measured on one character, the folder held 102 files, 76 of them with a
// catalogue number, of which 55 were on the page and 21 were not — the wiki
// does not catalogue every label. Showing "已擁有 55" next to a grid of 102
// looks like 47 films went missing.
const wiki = ref(null)
const wikiOpen = ref(false)
// { section } — the "not yet acquired" list. Its own dialog rather than a
// filter, because these are films with no file to show: 695 of them on one
// real character, and what the user wants from the row is the catalogue
// number to go and search with.
const missingFor = ref(null)

// Clicking a number in the panel filters the grid to those files. A list of
// names would say the same thing and let you do nothing with it -- the grid
// already has play, contact sheet, clip and delete on every card, which is
// the point of asking "which files are these".
function pickWiki(mode) {
  filterMode.value = filterMode.value === mode ? 'all' : mode
  // Not remembered: unlike 未看過, this filter is about one character and
  // means nothing on the next one.
  wikiOpen.value = false
}

// Text for the chip, so it is never a mystery which of the panel's numbers is
// currently applied.
const WIKI_LABELS = {
  'wiki:extra': '都沒收錄', 'wiki:dupe': '重複的檔案',
  'wiki:none': '認不出番號',
}
// the panel's sources, one merged list (core/catalog.py)
const SOURCE_NAME = { wiki: 'seesaawiki', avbase: 'AVBase' }
const wikiLabel = computed(() => {
  const m = filterMode.value
  if (!isWikiMode(m)) return ''
  if (m.startsWith('wiki:sec:')) return sectionName(m.slice('wiki:sec:'.length))
  return WIKI_LABELS[m] || m
})

// A character change must drop it — the numbers belonged to the old one.
watch([() => props.root, () => props.scope, () => props.merged], () => {
  if (isWikiMode(filterMode.value)) filterMode.value = 'all'
  ownerMode.value = 'all'
})

async function loadCostarList() {
  if (!props.root && !props.merged) { costarVideos.value = []; return }
  const q = props.merged ? `merged=${encodeURIComponent(props.merged)}`
                         : `root=${encodeURIComponent(props.root)}`
  try { costarVideos.value = (await api(`/api/costar/videos?${q}`)).videos || [] }
  catch { costarVideos.value = [] }
}

// ── watched / favourite ────────────────────────────────────────────────────
// A mark that cannot be filtered on is decoration. Remembered per browser, not
// per folder: "show me what I have not seen" is a mode you are in, not a
// property of the character you happen to be looking at.
const LS_FILTER = 'vam.videos.filter'
const filterMode = ref(localStorage.getItem(LS_FILTER) || 'all')

function setFilter(m) {
  filterMode.value = filterMode.value === m ? 'all' : m
  localStorage.setItem(LS_FILTER, filterMode.value)
}

// Counts for the buttons: without them "未看過" is a leap of faith, and an
// empty result reads as broken rather than as "you have seen them all".
const markCounts = computed(() => ({
  all: ownerBase.value.length,
  unwatched: ownerBase.value.filter(v => !v.watched).length,
  fav: ownerBase.value.filter(v => v.fav).length,
}))

// The mark travels with the row in `videos`, so the card, the filter and the
// heart in the player all read the same object and update together.
function applyMark(path, patch) {
  for (const list of [videos.value, costarVideos.value]) {
    const v = list.find(x => x.path === path)
    if (v) Object.assign(v, patch)
  }
}

async function onWatched(v) {
  if (!v || v.watched) return
  applyMark(v.path, { watched: true })     // optimistic: the mark is cosmetic
  try { await postWatched(api, v.path, true) }
  catch { applyMark(v.path, { watched: false }) }
}

async function toggleFav(v) {
  if (!v) return
  const next = !v.fav
  applyMark(v.path, { fav: next })
  try { await postFav(api, v.path, next) }
  catch (e) {
    applyMark(v.path, { fav: !next })
    setStatus('最愛儲存失敗：' + errText(e), 'warn')
  }
}

const hasReleased = computed(() => ownerBase.value.some(v => v.released))
const SORTS = computed(() =>
  ALL_SORTS.filter(([k]) => k !== 'released' || hasReleased.value))

const sorted = computed(() => {
  // still a copy: `sort` is in place, and for mode 'all' the filter hands back
  // the very array `videos` holds
  // the wiki numbers count this folder's own files, so their filters do too
  const s = [...filterVideos(isWikiMode(filterMode.value) ? videos.value
                               : ownerBase.value, filterMode.value)]
  if (sortBy.value === 'name') {
    // digit runs as numbers, as File Explorer does: `_21` before `_2080`
    s.sort((a, b) => naturalCompare(a.name, b.name))
  } else if (sortBy.value === 'size') {
    s.sort((a, b) => (a.size || 0) - (b.size || 0))
  } else if (sortBy.value === 'released') {
    // Stored as YYYY-MM-DD, so a string compare is a chronological one.
    s.sort((a, b) => (a.released || '').localeCompare(b.released || ''))
  } else {
    s.sort((a, b) => (a.mtime || 0) - (b.mtime || 0))
  }
  const out = sortDir.value === 'desc' ? s.reverse() : s
  // Undated films go last whichever way round: they are not "oldest", they
  // are unknown, and letting the reverse float them to the top would put the
  // least informative rows in front of the ones you chose this sort to see.
  if (sortBy.value === 'released') {
    return [...out.filter(v => v.released), ...out.filter(v => !v.released)]
  }
  return out
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const url = props.scope
      ? `/api/videos?scope=${encodeURIComponent(props.scope)}`
      : props.merged
        ? `/api/videos?merged=${encodeURIComponent(props.merged)}`
        : props.root
          ? `/api/videos?root=${encodeURIComponent(props.root)}`
          : '/api/videos'
    const d = await api(url)
    videos.value = d.videos
    wiki.value = d.wiki || null
    loadCostarList()
  } catch (e) {
    error.value = '載入失敗：' + errText(e)
  } finally {
    loading.value = false
  }
}

const emit = defineEmits(['select', 'archive-folder', 'batch'])

// ── selection mode (inbox only) ────────────────────────────────────────────
const selectMode = ref(false)
const selected = ref(new Set())
const lastIdx = ref(null)

// flat order of the currently displayed videos, for shift-range selection
const flatOrder = computed(() => {
  if (!grouped.value) return sorted.value
  return [...grouped.value.loose, ...grouped.value.folders.flatMap(([, v]) => v)]
})

function isSel(p) { return selected.value.has(p) }

function toggleSel(v, ev) {
  const order = flatOrder.value
  const idx = order.findIndex(x => x.path === v.path)
  const next = new Set(selected.value)
  if (ev?.shiftKey && lastIdx.value !== null) {
    const [a, b] = [lastIdx.value, idx].sort((x, y) => x - y)
    for (let i = a; i <= b; i++) next.add(order[i].path)
  } else {
    next.has(v.path) ? next.delete(v.path) : next.add(v.path)
    lastIdx.value = idx
  }
  selected.value = next
  emitSel()
}

function selectFolder(items) {
  const next = new Set(selected.value)
  const allIn = items.every(v => next.has(v.path))
  items.forEach(v => allIn ? next.delete(v.path) : next.add(v.path))
  selected.value = next
  emitSel()
}

function clearSel() { selected.value = new Set(); lastIdx.value = null; emitSel() }

function emitSel() {
  const items = flatOrder.value.filter(v => selected.value.has(v.path))
  emit('batch', items)
}

// derive the folder path of a group from one of its files
function folderPath(items) {
  const p = items[0].path
  const cut = p.lastIndexOf('\\') >= 0 ? '\\' : '/'
  // group is the FIRST level under an inbox root, so climb to that level:
  // the API told us the group name; rebuild path up to it
  const g = items[0].group
  const idx = p.toLowerCase().indexOf(cut + g.toLowerCase() + cut)
  return idx >= 0 ? p.slice(0, idx + 1 + g.length) : p.slice(0, p.lastIndexOf(cut))
}

function toggleSelectMode() {
  selectMode.value = !selectMode.value
  if (!selectMode.value) clearSel()
}
defineExpose({ toggleSelectMode, clearSel, selectMode, remove, reload: load,
               applyMark })
const note = ref('')
const noteSaved = ref(false)
const canEdit = computed(() => store.user?.can_edit && store.editMode)

async function loadNote(path) {
  note.value = ''
  try {
    const n = await api(`/api/note?path=${encodeURIComponent(path)}`)
    note.value = n.note || ''
  } catch { /* ignore */ }
}

// ── film title ─────────────────────────────────────────────────────────────
// Two keys, and the server picks: a catalogue number when the file has one, so
// the edit covers every copy of that film, otherwise the path, which is all
// 95% of this library has. See backend/app/core/filetitle.py.
const titleStr = ref('')
const fetchedTitle = ref('')
const titleSaved = ref(false)

function loadTitle(v) {
  titleStr.value = v.title || ''
  fetchedTitle.value = v.title || ''
}

async function saveTitle() {
  // Hold the row rather than reading `playing` again after the await. This
  // fires on blur, and the most ordinary way to blur the field is to close
  // the player — which sets `playing` to null while the request is still in
  // flight. Reading it afterwards threw "Cannot set properties of null".
  // Closing the player must still save what was typed, so this captures and
  // carries on rather than bailing out.
  const cur = playing.value
  if (!canInfo.value || !cur) return
  if (titleStr.value === (cur.title || '')) return
  try {
    // A catalogue number is the better key when there is one, so send it and
    // let the path be the fallback — 95% of this library has no number.
    const d = await api('/api/movietitle/set', {
      method: 'POST',
      body: JSON.stringify({ video_id: cur.video_id || '',
                             path: cur.path,
                             manual: titleStr.value }),
    })
    // A number covers every copy at once; a path covers exactly one file.
    // The rule lives in src/filetitle.js so it can be tested — the failure it
    // guards against (every un-numbered video sharing video_id '') would stamp
    // one title across most of the page.
    for (const v of titleTargets(videos.value, d)) v.title = d.title
    cur.title = d.title
    titleStr.value = d.title
    titleSaved.value = true
    setTimeout(() => { titleSaved.value = false }, 1500)
  } catch (e) {
    setStatus('片名儲存失敗：' + errText(e), 'warn')
  }
}

// ── release date ───────────────────────────────────────────────────────────
// Typed by hand for any video, not only the imported JAV ones. The server
// turns whatever was typed into YYYY-MM-DD (or YYYY / YYYY-MM when that is all
// that is known) and refuses what it cannot read, so the box shows the stored
// form after saving. Same two keys and the same row rule as the title. A typed
// date wins over an imported one; clearing the box falls back to the import.
// See backend/app/core/releasedate.py.
const relStr = ref('')
const relSaved = ref(false)
const relErr = ref('')

function loadReleased(v) {
  relStr.value = v.released || ''
  relErr.value = ''
}

async function saveReleased() {
  const cur = playing.value                     // captured: blur may close it
  if (!canInfo.value || !cur) return
  if (relStr.value.trim() === (cur.released || '')) { relErr.value = ''; return }
  try {
    const d = await api('/api/released/set', {
      method: 'POST',
      body: JSON.stringify({ video_id: cur.video_id || '', path: cur.path,
                             released: relStr.value }),
    })
    for (const list of [videos.value, costarVideos.value]) {
      for (const v of titleTargets(list, d)) v.released = d.released
    }
    cur.released = d.released
    if (playing.value === cur) relStr.value = d.released
    relErr.value = ''
    relSaved.value = true
    setTimeout(() => { relSaved.value = false }, 1500)
  } catch (e) {
    relErr.value = errText(e)
  }
}

async function saveNote() {
  if (!canInfo.value || !playing.value) return
  try {
    await api('/api/note', {
      method: 'POST',
      body: JSON.stringify({ path: playing.value.path, note: note.value }),
    })
    noteSaved.value = true
    setTimeout(() => { noteSaved.value = false }, 1500)
  } catch { /* ignore */ }
}

// ── delete mode (character folders) ────────────────────────────────────────
const canInfo = computed(() => store.editMode && can('edit_info'))

// ── co-stars (共演) ─────────────────────────────────────────────────────────
// Who else is in the video playing. Marked here, listed under 「共演」 on
// their own page. A co-star with no character folder is kept by name and
// links up by itself once a folder (or an alias) for that name exists. Shown
// in one 演出 row with the film's whole cast from the stored lists (wiki /
// AVBase import, javdatabase), each one a click away from being marked.
const costars = ref([])          // [{ folder, name, kind: 'folder'|'name', typed? }]
const cast = ref([])             // [{ name, folder, char, lead, marked }]
const costarInput = ref('')
const charList = ref([])         // for the name box's suggestions

function showCostar(d) {
  costars.value = d.costars || []
  cast.value = d.cast || []
}

async function loadCostar(v) {
  costars.value = []
  cast.value = []
  costarInput.value = ''
  try {
    const d = await api(`/api/costar?path=${encodeURIComponent(v.path)}`)
    // a different video may have been opened while this was in flight
    if (playing.value?.path !== v.path) return
    showCostar(d)
  } catch { /* the row just stays empty */ }
  if (canInfo.value && !charList.value.length) {
    try { charList.value = (await api('/api/characters')).characters || [] }
    catch { /* typing a name then simply finds nothing */ }
  }
}

async function saveCostars(list) {
  const cur = playing.value
  if (!cur) return
  try {
    const d = await api('/api/costar/set', {
      method: 'POST',
      body: JSON.stringify({
        path: cur.path,
        folders: list.filter(c => c.kind !== 'name').map(c => c.folder),
        names: list.filter(c => c.kind === 'name').map(c => c.typed),
      }),
    })
    if (playing.value?.path === cur.path) showCostar(d)
    // this page may be one of the co-stars, so its 共演 list may have changed
    loadCostarList()
    // a name-only co-star is searchable
    if (list.some(c => c.kind === 'name')) loadVideoIndex(true)
  } catch (e) {
    setStatus('共演儲存失敗：' + errText(e), 'warn')
  }
}

const sameChip = (a, b) => a.kind === b.kind &&
  (a.kind === 'name' ? a.typed === b.typed : a.folder === b.folder)

function addCostar(c) {
  if (costars.value.some(x => x.folder && x.folder === c.folder)) return
  saveCostars([...costars.value, { folder: c.folder, name: c.name, kind: 'folder' }])
}

function addName(n) {
  if (costars.value.some(x => x.typed === n || x.name === n)) return
  saveCostars([...costars.value, { folder: '', name: n, kind: 'name', typed: n }])
}

function removeCostar(c) {
  saveCostars(costars.value.filter(x => !sameChip(x, c)))
}

// The player's one 演出 row: the cast in order, a marked member carrying her
// chip (`chip` is the server's index into `costars`), then the chips no cast
// list names. Marked twice used to mean listed twice.
const castLine = computed(() => {
  const used = new Set()
  const line = cast.value.map(c => {
    const chip = c.chip >= 0 ? costars.value[c.chip] : null
    if (chip) used.add(c.chip)
    return { ...c, chip, key: 'c:' + c.name }
  })
  costars.value.forEach((x, i) => {
    if (!used.has(i)) line.push({ name: x.name, chip: x, key: x.kind + ':' + (x.typed || x.folder) })
  })
  return line
})

// one name from the cast line: the character it matches, else the name itself
function addFromCast(c) {
  if (c.folder) addCostar({ folder: c.folder, name: c.char })
  else addName(c.name)
}

// the lead is whoever's folder the file is already in
function isUnder(path, folder) {
  const p = path.toLowerCase().replace(/\\/g, '/')
  const f = folder.toLowerCase().replace(/\\/g, '/').replace(/\/$/, '')
  return p.startsWith(f + '/')
}

async function addTyped() {
  const n = costarInput.value.trim()
  if (!n || !playing.value) return
  const low = n.toLowerCase()
  let c = charList.value.find(x => x.name === n) ||
          charList.value.find(x => x.name.toLowerCase() === low)
  if (!c) {
    // an alias finds her too — the server knows the aliases, this list does not
    try {
      const r = await api('/api/costar/resolve', { method: 'POST', body: JSON.stringify({ name: n }) })
      if (r.folder) c = { path: r.folder, name: r.name, paths: r.paths }
    } catch { /* treated as a name below */ }
  }
  costarInput.value = ''
  if (!c) {
    // nobody's folder or alias: kept as a name, and linked up later by itself
    addName(n)
    setStatus(`已加入「${n}」（沒有角色資料夾；之後建了資料夾或加成別名會自動接上）`, 'ok')
    return
  }
  if ((c.paths || [c.path]).some(f => isUnder(playing.value.path, f))) {
    setStatus(`這部片本來就在「${c.name}」底下`, 'warn')
    return
  }
  addCostar({ folder: c.path, name: c.name })
}
const canArchive = computed(() => store.editMode && can('archive'))
const canDelete = computed(() => store.editMode && can('delete') &&
  props.scope !== 'inbox' && videos.value.length > 0)
// Its own tick now, so it no longer rides on canDelete.
const canClip = computed(() => store.editMode && can('clip') &&
  props.scope !== 'inbox' && videos.value.length > 0)

async function doDelete(v) {
  // Say so before asking for a confirmation that cannot be honoured. The
  // badge already says the video is a favourite, but the trash button is
  // right there and the point of the guard is to be understood, not obeyed.
  const guard = favGuard(v, store.user?.role)
  let force = false
  if (guard.kind === 'own' || (guard.kind === 'others' && !guard.canForce)) {
    alert(favGuardMessage(v.name, guard))
    return
  }
  if (guard.kind === 'others') {
    // The admin path: this *is* the confirmation, so no second one follows.
    if (!confirm(favGuardMessage(v.name, guard))) return
    force = true
  } else if (!confirm(`確定永久刪除？此操作無法復原。\n\n${v.name}\n${fmtSize(v.size)}`)) return
  try {
    // an open player holds the file, and Windows will not delete it then
    if (playing.value?.path === v.path) await releaseFile(close)
    // A single-path delete raises rather than returning an error list (the
    // endpoint only returns one for `paths`), so the catch below covers a
    // refusal — the card is dropped only on a real success.
    await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ path: v.path, force_fav: force }),
    })
    remove(v.path)
    setStatus(`已刪除 ${v.name}`, 'warn')
  } catch (e) {
    setStatus('刪除失敗：' + errText(e), 'warn')
    alert('刪除失敗：' + errText(e))
  }
}

function open(v, ev) {
  if (selectMode.value) { toggleSel(v, ev); return }
  // in the inbox the click selects the video for the archive preview panel;
  // elsewhere it opens the player directly
  if (props.scope === 'inbox') emit('select', v)
  else startPlaying(v)
}
function startPlaying(v, at = 0) {
  playStart.value = at
  playing.value = v; loadNote(v.path); loadTitle(v); loadReleased(v); loadSub(v.path); loadCostar(v)
  loadRelations(v)
}

// ── 收錄 (AVBase's relations) ──────────────────────────────────────────────
// What this film is cut from, which compilations hold it, what is cut from
// it — knowing a film includes knowing that (the user's framing, 2026-10-27:
// not a hunt for redundant files). One you have plays from here; one you do
// not copies its number.
const relations = ref([])
const relAll = ref(false)
const REL_SHOWN = 6           // a compilation can relate to fifty films

async function loadRelations(v) {
  relations.value = []
  relAll.value = false
  if (!v.video_id) return               // relations are between numbers
  try {
    const d = await api(`/api/relations?path=${encodeURIComponent(v.path)}`)
    if (playing.value?.path === v.path) relations.value = d.relations || []
  } catch { /* the row just stays away */ }
}

function playRelated(r) {
  startPlaying({ path: r.have.path, name: r.have.name, video_id: r.other,
                 title: r.title, released: r.date, size: 0, mtime: 0 })
}

async function copyId(id) {
  try {
    await navigator.clipboard.writeText(id)
    setStatus(`已複製番號 ${id}`, 'ok')
  } catch { prompt('複製：', id) }
}
function close() { playing.value = null; playStart.value = 0; dropSub() }

// 「從這裡播放」 on a contact-sheet cell. The start rides on the URL as a
// media fragment (#t=), which every browser honours on load — VideoPlayer
// needs no new prop and never seeks on its own.
const playStart = ref(0)
const playSrc = computed(() => playing.value
  ? streamUrl(playing.value.path) + (playStart.value > 0 ? `#t=${playStart.value}` : '')
  : '')
function playFromSheet(t) {
  if (sheetFor.value) startPlaying(sheetFor.value, t || 0)
}

// ── subtitles ──────────────────────────────────────────────────────────────
// Probed here rather than in VideoPlayer, which is deliberately free of the API
// (see its own comment); it takes the finished URL as a prop.
//
// Asked per playback rather than returned with the listing: 31 of 22,183 videos
// in this library have a subtitle, so answering it for every row would be
// thousands of stat calls to say "no" 99.86% of the time.
//
// A blob rather than pointing the <track> straight at /api/subtitle: the URL has
// to be *known good* before the element exists, because a <track> whose source
// 404s still gives the browser a subtitle button that does nothing.
const subUrl = ref(null)

function dropSub() {
  if (subUrl.value) URL.revokeObjectURL(subUrl.value)
  subUrl.value = null
}

async function loadSub(path) {
  dropSub()
  try {
    // no-store on the request, not just the response. The endpoint used to send
    // a month-long Cache-Control, and a cached entry that is still fresh is
    // used without asking the server at all — so a browser that loaded a
    // subtitle before that header was fixed would keep serving the old text
    // for a month. Caught exactly that way in testing.
    const r = await fetch(subtitleUrl(path), { cache: 'no-store' })
    if (!r.ok) return                    // 404 is the normal answer
    const text = await r.text()
    // The player may have been closed, or another video opened, while this was
    // in flight — publishing now would put one video's subtitle on another.
    if (playing.value?.path !== path) return
    subUrl.value = URL.createObjectURL(new Blob([text], { type: 'text/vtt' }))
  } catch { /* no subtitle, no track, no message */ }
}

onBeforeUnmount(dropSub)

// let the parent drop a video from the list after archive/delete
function remove(path) {
  videos.value = videos.value.filter(v => v.path !== path)
  costarVideos.value = costarVideos.value.filter(v => v.path !== path)
}

// inbox: split into loose files and per-subfolder sections
// ── incremental rendering ──────────────────────────────────────────────────
// The inbox holds 2,016 videos, and every one used to become a card up front:
// 16,128 DOM nodes, 161 ms to build on a desktop browser and several times
// that on a phone — plus the same cost again to tear down when you navigate
// away, which is what made returning to the home page sit on "載入中".
// Rendering a screenful at a time costs nothing on the small lists where the
// old behaviour was already fine.
const BATCH = 60
const limit = ref(BATCH)
const visible = computed(() => sorted.value.slice(0, limit.value))
const hasMore = computed(() => limit.value < sorted.value.length)
function showMore() { limit.value += BATCH }

// A new list, a different sort, or a changed folder all start again from the
// top of the page, so they must start again from the first batch too.
watch([() => props.scope, () => props.root, () => props.merged,
       sortBy, sortDir, filterMode, videos], () => { limit.value = BATCH })

let io = null
const sentinel = ref(null)
watch(sentinel, el => {
  io?.disconnect()
  if (!el) return
  // 400px of lead time: the next batch is built before the blank space is
  // reached, so scrolling never visibly stalls
  io = new IntersectionObserver(es => { if (es[0].isIntersecting) showMore() },
                                { rootMargin: '400px' })
  io.observe(el)
})

const grouped = computed(() => {
  if (props.scope !== 'inbox') return null
  const loose = visible.value.filter(v => !v.group)
  const map = new Map()
  for (const v of visible.value) {
    if (!v.group) continue
    if (!map.has(v.group)) map.set(v.group, [])
    map.get(v.group).push(v)
  }
  return { loose, folders: [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])) }
})

watch(() => [props.root, props.scope, props.merged], load)

// A search hit in the sidebar names one file; landing on a character with a
// few hundred videos and leaving the user to find it by eye would waste the
// search. Scroll it into view and flash it, then release the highlight.
const focusPath = ref(null)
let focusTimer = null

watch(() => store.focusFile, async path => {
  if (!path) return
  store.focusFile = null                 // one-shot; a remount must not re-fire
  focusPath.value = path
  // the list may still be loading when the click arrives
  for (let i = 0; i < 20 && !videos.value.length; i++) {
    await new Promise(r => setTimeout(r, 100))
  }
  // The card has to exist before it can be scrolled to, and with incremental
  // rendering the 900th result is not on the page yet. Render up to it —
  // otherwise searching for a file silently does nothing, which is worse than
  // the slow list this change was meant to fix.
  const at = sorted.value.findIndex(v => v.path === path)
  if (at >= limit.value) limit.value = at + BATCH
  await nextTick()
  const el = document.querySelector(`[data-path="${CSS.escape(path)}"]`)
  el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  clearTimeout(focusTimer)
  focusTimer = setTimeout(() => { focusPath.value = null }, 2600)
}, { immediate: true })

onBeforeUnmount(() => {
  clearTimeout(focusTimer)
  io?.disconnect()        // the observer outlives the component otherwise
})

onMounted(load)
// A convert run can replace files in place (same path, new content): list
// again so the cards and sheets pick up the new mtime.
watch(() => store.filesVersion, load)
</script>

<template>
  <div class="grid-wrap">
    <div class="count" :class="{ danger: store.deleteMode }">
      <span class="ib"><Icon name="film" :size="13" />
        {{ videos.length + costarVideos.length }} 部影片<template
          v-if="costarVideos.length">（含共演 {{ costarVideos.length }}）</template></span>
      <button v-if="wiki" class="wbtn" :class="{ on: wikiOpen }"
              :title="`收錄 ${wiki.total} 部`"
              @click="wikiOpen = !wikiOpen"><Icon name="info" :size="13" /></button>
      <!-- not on a phone: the header's mode button already says 編輯模式, and
           here it pushed the sort buttons onto a third line -->
      <span v-if="store.editMode" class="mode-tag no-mobile">編輯模式</span>
      <span v-if="store.deleteMode" class="del-tag ib">
        <Icon name="warn" :size="12" /> 刪除模式 — 點垃圾桶會永久刪除</span>
      <span v-if="store.clipMode" class="clip-tag ib no-mobile">
        <Icon name="scissors" :size="12" /> 剪輯模式 — 點剪刀選取要保留的片段</span>
      <span class="spacer"></span>
      <button v-if="canDelete" class="tbtn delbtn" :class="{ on: store.deleteMode }"
              @click="store.deleteMode = !store.deleteMode">
        <Icon name="trash" :size="12" />
        {{ store.deleteMode ? '結束刪除' : '刪除模式' }}
      </button>
      <button v-if="canClip" class="tbtn clipbtn no-mobile" :class="{ on: store.clipMode }"
              @click="store.clipMode = !store.clipMode">
        <Icon name="scissors" :size="12" />
        {{ store.clipMode ? '結束剪輯' : '剪輯模式' }}
      </button>
      <!-- Kept out of the `videos.length > 1` block below on purpose: the
           filter is a mode you carry between folders, so it has to be visible
           (and clearable) even where it has left one video showing. The counts
           are what stop an empty result reading as a broken page. -->
      <span v-if="videos.length || costarVideos.length" class="tgroup fgroup">
        <span class="slab no-mobile">顯示</span>
        <button v-for="f in FILTERS" :key="f[0]" class="tbtn markbtn"
                :class="{ on: filterMode === f[0], zero: !markCounts[f[0]] }"
                :title="f[1]" @click="setFilter(f[0])">
          <!-- On a phone this group shares the line with the count, so the
               longest label becomes the heart the cards already use. -->
          <template v-if="f[0] === 'fav'"><span class="no-mobile">{{ f[1] }}</span><Icon
            name="heart" :size="11" class="mobile-only" /></template>
          <template v-else>{{ f[1] }}</template>
          <span class="mcnt">{{ markCounts[f[0]] }}</span>
        </button>
      </span>
      <!-- 共演: only where there are any. Narrows the mixed listing to this
           folder's own files or to the ones filed under other characters. -->
      <span v-if="costarVideos.length" class="tgroup ogroup">
        <span class="slab no-mobile">來源</span>
        <button v-for="o in OWNER_MODES" :key="o[0]" class="tbtn markbtn"
                :class="{ on: ownerMode === o[0] }"
                :title="o[0] === 'costar' ? '這位角色出現在其他角色資料夾裡的影片' : o[1]"
                @click="ownerMode = o[0]">
          {{ o[1] }}
          <span class="mcnt">{{ o[0] === 'own' ? videos.length
                                : o[0] === 'costar' ? costarVideos.length
                                : videos.length + costarVideos.length }}</span>
        </button>
      </span>
      <!-- phone only: ends the first line (count + filters) -->
      <span class="brk"></span>
      <button v-if="wikiLabel" class="tbtn wchip ib" @click="filterMode = 'all'">
        <Icon name="info" :size="11" /> {{ wikiLabel }}
        <span class="mcnt">{{ sorted.length }}</span>
        <Icon name="close" :size="11" />
      </button>
      <span v-if="ownerBase.length > 1" class="tgroup sgroup">
        <span class="slab">排序</span>
        <button v-for="s in SORTS"
                :key="s[0]" class="tbtn ib" :class="{ on: sortBy === s[0] }"
                @click="setSort(s[0])">
          {{ s[1] }}
          <Icon v-if="sortBy === s[0]" :name="sortDir === 'asc' ? 'up' : 'down'" :size="11" />
        </button>
      </span>
    </div>

    <!-- The breakdown, because the total on its own is misread: on one page
         750 releases were 543 単体, 81 VR, 113 配信 and 5 総集編, which is not
         "she filmed 750 films". Photobook and appearance sections are already
         excluded by the parser. -->
    <div v-if="wiki && wikiOpen" class="wpanel">
      <!-- One list from every imported page (seesaawiki, AVBase), merged by
           catalogue number — core/catalog.py. -->
      <div class="wh">
        {{ wiki.page_name || '' }} — 收錄
        <template v-for="pg in (wiki.pages || [{ source: 'wiki', url: wiki.url }])"
                  :key="pg.source">
          <a v-if="pg.url" :href="pg.url" target="_blank"
             rel="noopener noreferrer" class="wsrc ib"
             :title="pg.listed ? `這個來源列了 ${pg.listed} 部` : ''">
            <Icon name="link" :size="11" /> {{ SOURCE_NAME[pg.source] || pg.source }}</a>
          <span v-else class="wsrc">{{ SOURCE_NAME[pg.source] || pg.source }}</span>
        </template>
      </div>
      <table>
        <thead>
          <tr class="hd"><th></th><th>收錄</th><th>尚未收入</th>
            <th>資料夾</th></tr>
        </thead>
        <tbody>
        <tr v-for="sec in wiki.sections" :key="sec.name">
          <td>{{ sectionName(sec.name) }}</td>
          <td class="n">{{ sec.count }}</td>
          <td class="n"><button class="num"
              :class="{ zero: sec.count === sec.have }"
              :disabled="sec.count === sec.have"
              @click="missingFor = { section: sec.name }"
              >{{ sec.count - sec.have }}</button></td>
          <!-- Dimmed at zero so the eye goes to the sections you actually
               have something in; 0 is by far the common value. -->
          <td class="n"><button class="num" :class="{ zero: !sec.have }"
              :disabled="!sec.have" @click="pickWiki('wiki:sec:' + sec.name)"
              >{{ sec.have }}</button></td>
        </tr>
        <tr class="tot">
          <td>総計</td>
          <td class="n">
            {{ wiki.total }}
            <!-- A subset of the total, not something to add to it, so it sits
                 against the number it qualifies rather than on its own row. -->
            <span v-if="wiki.no_id" class="qual">（{{ wiki.no_id }} 部無番號）</span>
          </td>
          <td class="n"><button class="num"
              :class="{ zero: wiki.total === wiki.have }"
              :disabled="wiki.total === wiki.have"
              @click="missingFor = { section: '' }"
              >{{ wiki.total - wiki.have }}</button></td>
          <td class="n" :class="{ zero: !wiki.have }">{{ wiki.have }}</td>
        </tr>

        <!-- Where the rest of the files went. The four values below the gap
             add up to the file count exactly; that is the whole point of the
             section, and 重複的檔案 is the one nobody thinks of -- two copies
             of one film are one work above but two rows in the grid. -->
        <tr class="gap"><td colspan="4"></td></tr>
        <tr><td>都沒收錄</td><td class="n dash">—</td><td class="n dash">—</td>
            <td class="n"><button class="num" :class="{ zero: !wiki.extra }"
                :disabled="!wiki.extra" @click="pickWiki('wiki:extra')"
                >{{ wiki.extra }}</button></td></tr>
        <tr><td>重複的檔案
            <!-- The panel counts surplus copies; the filter shows every copy,
                 because choosing between them means seeing them together. -->
            <span v-if="wiki.dupe_files > wiki.dupes" class="qual"
                  >點開看全部 {{ wiki.dupe_files }} 個</span></td>
            <td class="n dash">—</td><td class="n dash">—</td>
            <td class="n"><button class="num" :class="{ zero: !wiki.dupes }"
                :disabled="!wiki.dupes" @click="pickWiki('wiki:dupe')"
                >{{ wiki.dupes }}</button></td></tr>
        <tr><td>認不出番號</td><td class="n dash">—</td><td class="n dash">—</td>
            <td class="n"><button class="num" :class="{ zero: !wiki.no_number }"
                :disabled="!wiki.no_number" @click="pickWiki('wiki:none')"
                >{{ wiki.no_number }}</button></td></tr>
        <tr class="tot">
          <td>資料夾影片合計</td><td class="n dash">—</td><td class="n dash">—</td>
          <td class="n">{{ wiki.files ?? videos.length }}</td>
        </tr>
        </tbody>
      </table>
      <div class="wn">
        <span class="dim">「收錄」不等於「主演」—— 裡面包含總集編與多人企劃片。</span>
      </div>
    </div>

    <div v-if="loading" class="msg">載入中…</div>
    <div v-else-if="error" class="msg err">{{ error }}</div>

    <!-- inbox: loose files first, then one section per subfolder -->
    <template v-else-if="grouped">
      <div v-if="grouped.loose.length" class="grid" :style="gvars">
        <div v-for="v in grouped.loose" :key="v.path" class="card"
             :data-path="v.path"
             :class="{ sel: isSel(v.path), focused: focusPath === v.path }"
             @click="e => open(v, e)">
          <span v-if="selectMode" class="cbox"><Icon :name="isSel(v.path) ? 'checked' : 'unchecked'" :size="16" /></span>
          <div class="thumb"><img :src="thumbFor(v)" loading="lazy" />
              <div class="badges">
                <button v-if="canSheet(v)" class="tb sheetbtn" title="縮圖表（多個時間點的畫面）"
                        @click="e => openSheet(v, e)">
                  <Icon name="grid" :size="14" /></button>
              </div>
              <div v-if="markIcons(v).length" class="marks">
                <Icon v-for="m in markIcons(v)" :key="m.key" :name="m.icon"
                      :size="13" :class="m.cls" :title="m.title" />
              </div>
              <div v-if="relTag(v)" class="reltag" :title="relTag(v).title">{{ relTag(v).text }}</div>
            </div>
          <div class="meta">
            <div class="name" :title="v.name">{{ stem(v.name) }}</div>
            <div class="vtitle" :title="v.title">{{ v.title }}</div>
            <!-- Size left, release date pushed to the right edge: they are
               different kinds of fact and reading one should not mean
               scanning past the other. -->
          <div class="sub">{{ fmtSize(v.size) }}
            <span v-if="v.released" class="rel">{{ v.released }}</span>
          </div>
          </div>
        </div>
      </div>
      <template v-for="[folder, items] in grouped.folders" :key="folder">
        <div class="fhdr">
          <span class="ftitle ib"><Icon name="folder" :size="14" /> {{ folder }} <span class="fcnt">({{ items.length }})</span></span>
          <button v-if="selectMode" class="fbtn" @click="selectFolder(items)">全選此資料夾</button>
          <button v-else-if="canArchive" class="fbtn ib"
                  @click="emit('archive-folder', folderPath(items))">
            <Icon name="package" :size="13" /> 整包歸檔</button>
        </div>
        <div class="grid" :style="gvars">
          <div v-for="v in items" :key="v.path" class="card"
               :data-path="v.path"
               :class="{ sel: isSel(v.path), focused: focusPath === v.path }"
               @click="e => open(v, e)">
            <span v-if="selectMode" class="cbox"><Icon :name="isSel(v.path) ? 'checked' : 'unchecked'" :size="16" /></span>
            <div class="thumb"><img :src="thumbFor(v)" loading="lazy" />
              <div class="badges">
                <button v-if="canSheet(v)" class="tb sheetbtn" title="縮圖表（多個時間點的畫面）"
                        @click="e => openSheet(v, e)">
                  <Icon name="grid" :size="14" /></button>
              </div>
              <div v-if="markIcons(v).length" class="marks">
                <Icon v-for="m in markIcons(v)" :key="m.key" :name="m.icon"
                      :size="13" :class="m.cls" :title="m.title" />
              </div>
              <div v-if="relTag(v)" class="reltag" :title="relTag(v).title">{{ relTag(v).text }}</div>
            </div>
            <div class="meta">
              <div class="name" :title="v.name">{{ stem(v.name) }}</div>
            <div class="vtitle" :title="v.title">{{ v.title }}</div>
              <!-- Size left, release date pushed to the right edge: they are
               different kinds of fact and reading one should not mean
               scanning past the other. -->
          <div class="sub">{{ fmtSize(v.size) }}
            <span v-if="v.released" class="rel">{{ v.released }}</span>
          </div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <div v-else class="grid" :style="gvars">
      <div v-for="v in visible" :key="v.path" class="card"
           :data-path="v.path"
           :class="{ armed: store.deleteMode, focused: focusPath === v.path }"
           @click="open(v)">
        <div class="thumb">
          <img :src="thumbFor(v)" loading="lazy" />
          <!-- one row, because these used to be stacked on the same corner
               coordinates and the sheet button was hidden under the delete
               badge whenever delete mode was on -->
          <div class="badges">
            <button v-if="canSheet(v)" class="tb sheetbtn" title="縮圖表（多個時間點的畫面）"
                    @click="e => openSheet(v, e)">
              <Icon name="grid" :size="14" /></button>
            <button v-if="store.clipMode" class="tb clipbadge no-mobile" title="剪輯片段"
                    @click="e => openClip(v, e)">
              <Icon name="scissors" :size="14" /></button>
            <!-- Still a button, not a disabled one: a dead control tells you
                 nothing, and the whole reason for the guard is that you
                 should learn why. Clicking explains and offers the remedy. -->
            <!-- Locked for anyone's favourite, not only yours: before
                 fav_others existed the admin saw an ordinary trash can on a
                 video someone else loved, and only learned otherwise after
                 clicking. -->
            <button v-if="store.deleteMode" class="tb delbadge"
                    :class="{ locked: v.fav || v.fav_others }"
                    :title="v.fav ? '已標為我的最愛，不會被刪除'
                           : v.fav_others ? `有 ${v.fav_others} 位使用者標為最愛`
                           : '永久刪除'"
                    @click.stop="doDelete(v)">
              <Icon :name="v.fav || v.fav_others ? 'heart' : 'trash'" :size="14" /></button>
          </div>
          <div v-if="markIcons(v).length" class="marks">
            <Icon v-for="m in markIcons(v)" :key="m.key" :name="m.icon"
                  :size="13" :class="m.cls" :title="m.title" />
          </div>
          <!-- above the 共演 tag when both are there: they share the corner -->
          <div v-if="relTag(v)" class="reltag" :class="{ uptag: v.lead }"
               :title="relTag(v).title">{{ relTag(v).text }}</div>
          <!-- whose folder a 共演 video actually lives in -->
          <div v-if="v.lead" class="leadtag"
               :title="`共演：這部片放在「${v.lead}」的資料夾`">共演・{{ v.lead }}</div>
        </div>
        <div class="meta">
          <div class="name" :title="v.name">{{ stem(v.name) }}</div>
            <div class="vtitle" :title="v.title">{{ v.title }}</div>
          <!-- Size left, release date pushed to the right edge: they are
               different kinds of fact and reading one should not mean
               scanning past the other. -->
          <div class="sub">{{ fmtSize(v.size) }}
            <span v-if="v.released" class="rel">{{ v.released }}</span>
          </div>
        </div>
      </div>
    </div>

    <WikiMissingDialog v-if="missingFor && wiki" :root="props.root"
                       :name="wiki.page_name" :section="missingFor.section"
                       @close="missingFor = null" />
    <!-- v-show while the player is up: closing the player lands back on the
         sheet where it was (the user's choice), so it must not be unmounted -->
    <SheetViewer v-if="sheetFor" v-show="!playing" :paused="!!playing"
                 :video="sheetFor" @close="sheetFor = null"
                 @clip="fromSheet" @play="playFromSheet" />
    <ClipDialog v-if="clipFor" :video="clipFor" :initial="clipInitial"
                @created="onClipCreated"
                @close="clipFor = null" />

    <!-- player modal -->
    <div v-if="playing" class="modal" :class="{ bare: screen.mobile }" @click.self="close">
      <!-- On a phone the video is all there is (the user's request): no
           frame, no title, no note fields. What stays is a way out and the
           heart — the viewer's own mark, which is used from the phone — as
           two floating buttons kept clear of the screen edge. The frame used
           to be taller than a sideways phone, which pushed 關閉 5px off the
           top of the screen. -->
      <div v-if="screen.mobile" class="barebtns">
        <button class="bbtn" :class="{ on: playing.fav }"
                :title="playing.fav ? '取消我的最愛' : '加入我的最愛'"
                @click="toggleFav(playing)"><Icon name="heart" :size="20" /></button>
        <button class="bbtn" title="關閉" @click="close"><Icon name="close" :size="22" /></button>
      </div>
      <div class="player-box">
        <div v-if="!screen.mobile" class="player-head">
          <span class="ptitle" :title="playing.name">{{ playing.name }}</span>
          <!-- On a phone the word costs more than it explains: an X on a
               modal is unambiguous, and the space goes to the filename, which
               is the only thing in this row that varies. `.no-mobile` is the
               same switch the toolbar already uses. -->
          <button class="btn ghost ib" @click="close" title="關閉">
            <Icon name="close" :size="14" /><span class="no-mobile">關閉</span></button>
        </div>
        <VideoPlayer :src="playSrc" :subtitle="subUrl" @close="close"
                     @watched="onWatched(playing)" />
        <!-- Shown for every video now, not only the ones with a catalogue
             number. What changes is the reach of the edit, and the label says
             so rather than leaving the user to find out:
               with a number → stored against the film, so every copy updates
               without one   → stored against this one file's path
             Only a numbered film has a fetched title to fall back to, so the
             "還原" button is meaningless elsewhere and stays hidden. -->
        <div v-if="!screen.mobile" class="noterow">
          <span class="nlab ib"><Icon name="film" :size="13" /> 片名</span>
          <input v-model="titleStr" :readonly="!canInfo"
                 :placeholder="fetchedTitle || '輸入片名…'" @blur="saveTitle" />
          <Icon v-if="titleSaved" name="check" :size="14" class="saved" />
          <template v-if="canInfo || playing.released">
            <span class="nlab ib">發行日</span>
            <input v-model="relStr" class="relin" :class="{ bad: relErr }"
                   :readonly="!canInfo" placeholder="年-月-日"
                   title="例如 2026-10-22。只知道年份或年月也可以（2014、2014-05）。清空就改回匯入的日期"
                   @blur="saveReleased" @keydown.enter.prevent="$event.target.blur()" />
            <Icon v-if="relSaved" name="check" :size="14" class="saved" />
          </template>
          <span class="thint">{{ playing.video_id ? '套用到所有同番號的檔案' : '只套用這個檔案' }}</span>
          <button v-if="canInfo && titleStr && playing.video_id" class="lnk"
                  title="清除自訂片名，改回查詢到的"
                  @click="titleStr = ''; saveTitle()">還原</button>
        </div>
        <div v-if="!screen.mobile && relErr" class="relerr">{{ relErr }}</div>
        <div v-if="!screen.mobile" class="noterow">
          <span class="nlab ib"><Icon name="note" :size="13" /> 備忘</span>
          <input v-model="note" :readonly="!canInfo"
                 placeholder="輸入備忘…" @blur="saveNote" />
          <Icon v-if="noteSaved" name="check" :size="14" class="saved" />
          <!-- Not behind 編輯模式: this is the viewer's own record, and on the
               phone the whole editing surface is hidden by design. -->
          <button class="favbtn" :class="{ on: playing.fav }"
                  :title="playing.fav ? '取消我的最愛' : '加入我的最愛'"
                  @click="toggleFav(playing)">
            <Icon name="heart" :size="17" />
          </button>
        </div>
        <!-- One row: the film's whole cast as the stored lists name it, each
             person once — the lead grey, a marked co-star as a chip in her
             place, the rest a click away — then the marks no list names, and
             the box. A film with no list is just the chips and the box. -->
        <div v-if="!screen.mobile && (cast.length || costars.length || canInfo)"
             class="noterow corow">
          <span class="nlab ib"><Icon name="user" :size="13" /> 演出</span>
          <span class="cochips">
            <template v-for="c in castLine" :key="c.key">
              <span v-if="c.lead" class="castname lead" title="主角">{{ c.name }}<span
                    v-if="c.char && c.char !== c.name" class="castchar">（{{ c.char }}）</span></span>
              <!-- A name with no folder is dashed: it is kept, searchable, and
                   becomes the character by itself once a folder or alias exists. -->
              <span v-else-if="c.chip" class="cochip" :class="{ nm: !c.chip.folder }"
                    :title="!c.chip.folder ? '共演；沒有角色資料夾，只記名字，之後建了資料夾或加成別名會自動接上'
                            : c.chip.typed && c.chip.typed !== c.chip.name ? `共演，以「${c.chip.typed}」標記`
                            : '共演'">{{ c.name }}<span v-if="c.char && c.char !== c.name"
                      class="castchar">（{{ c.char }}）</span>
                <button v-if="canInfo" class="cox" title="移除共演"
                        @click="removeCostar(c.chip)"><Icon name="close" :size="10" /></button>
              </span>
              <button v-else-if="canInfo && !c.marked" class="castname add"
                      :title="c.folder ? `加入共演：${c.char}` : '加入共演（沒有角色資料夾，只記名字）'"
                      @click="addFromCast(c)">＋ {{ c.name }}<span v-if="c.char && c.char !== c.name"
                        class="castchar">（{{ c.char }}）</span></button>
              <span v-else class="castname">{{ c.name }}</span>
            </template>
            <input v-if="canInfo" v-model="costarInput" class="coin"
                   list="vam-costar-chars" placeholder="輸入角色名或演員名加入…"
                   @change="addTyped" @keydown.enter.prevent="addTyped" />
          </span>
          <span v-if="canInfo" class="thint">{{ playing.video_id ? '套用到所有同番號的檔案' : '只套用這個檔案' }}</span>
        </div>
        <!-- How this film relates to others (AVBase): one you have plays, one
             you do not copies its number. -->
        <div v-if="!screen.mobile && relations.length" class="noterow corow relrow">
          <span class="nlab ib"><Icon name="link" :size="13" /> 收錄</span>
          <span class="rellist">
            <span v-for="r in (relAll ? relations : relations.slice(0, REL_SHOWN))"
                  :key="r.this_is + r.other" class="relitem">
              <span class="relverb">{{ relVerb(r.kind, r.this_is === 'parent') }}</span>
              <button v-if="r.have" class="relid have"
                      :title="`你有這部（在「${r.have.char}」）：點一下播放`"
                      @click="playRelated(r)">{{ r.other }} ▶</button>
              <button v-else class="relid" title="你沒有這部：點一下複製番號"
                      @click="copyId(r.other)">{{ r.other }}</button>
              <span class="reltitle" :title="r.title">{{ r.title }}</span>
              <span class="relmeta">{{ r.date }}<template v-if="relNote(r.kind)"> · {{ relNote(r.kind) }}</template></span>
            </span>
            <button v-if="relations.length > REL_SHOWN" class="relmore"
                    @click="relAll = !relAll">
              {{ relAll ? '收起' : `還有 ${relations.length - REL_SHOWN} 部` }}</button>
          </span>
        </div>
        <datalist id="vam-costar-chars">
          <option v-for="c in charList" :key="c.path" :value="c.name" />
        </datalist>
      </div>
    </div>

    <!-- Growing the list as this comes into view, rather than a "load more"
         button: the scroll position is the only signal needed, and a button
         would interrupt exactly the browsing it is meant to support. -->
    <div v-if="!loading && hasMore" ref="sentinel" class="more">
      顯示 {{ visible.length }} / {{ sorted.length }} · 繼續捲動載入更多
    </div>
  </div>
</template>

<style scoped>
.card.focused { outline: 2px solid var(--accent); outline-offset: 2px;
  animation: flash 2.4s ease-out; }
@keyframes flash {
  0%, 30% { background: color-mix(in srgb, var(--accent) 28%, transparent); }
  100%    { background: transparent; }
}

.grid-wrap { padding: 16px; }
.more { text-align: center; color: var(--dim); font-size: 12px; padding: 18px 0 28px; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) { .grid-wrap { padding: 8px; } }
/* A label and its buttons wrap as one unit. Left loose in the row, a narrow
   screen split them anywhere — 「排序」 at the end of one line, 名稱 alone on
   the next — and the toolbar read as scattered chips. */
.tgroup { display: inline-flex; align-items: center; gap: 6px; flex-wrap: nowrap; }
.tbtn { white-space: nowrap; }
.brk { display: none; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  /* Two lines on a phone (the user's layout), right-aligned groups:
       🎞 n 部影片 ⓘ                [全部][未看過][♥]
       [刪除模式]            排序 [最近新增][名稱][大小]
     `order` rearranges without touching the desktop DOM order. A group that
     still does not fit shrinks and scrolls sideways rather than wrapping
     inside itself or pushing the count off its line. */
  .count .spacer { display: none; }
  /* Basis 0 + grow: a group takes whatever its line has left instead of
     wrapping onto a line of its own when it is a few px too wide (the sort
     buttons did, next to 刪除模式). The ::before's auto margin pushes the
     buttons right; when they overflow it collapses to 0, so the start of
     the row is still reachable by scrolling (justify-content: flex-end would
     put it out of reach). */
  .tgroup { flex: 1 1 0; min-width: 0; overflow-x: auto; scrollbar-width: none; }
  .tgroup::before { content: ''; margin-left: auto; }
  .count > .ib:first-child, .delbtn { flex-shrink: 0; }
  .tgroup::-webkit-scrollbar { display: none; }
  .tgroup .slab { margin-left: 0; }
  .count > .ib:first-child { white-space: nowrap; }
  .fgroup { order: 1; }
  .brk { display: block; order: 2; flex-basis: 100%; height: 0;
    margin-top: -6px; }   /* its own zero-height line: cancel one row-gap */
  .del-tag { order: 3; flex-basis: 100%; }
  .wchip { order: 3; }
  .delbtn { order: 4; }
  .sgroup { order: 5; }
  .ogroup { order: 6; flex-basis: 100%; }
}
.count { display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  color: var(--dim); font-size: 13px; margin-bottom: 12px; }
.count .spacer { flex: 1; }
.slab { color: var(--dim); font-size: 11px; margin-left: 8px; }
.tbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 10px; cursor: pointer; }
.tbtn:hover { border-color: var(--accent); }
.tbtn.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.tbtn.delbtn { color: var(--danger); border-color: #5a2020; }
.tbtn.delbtn:hover { border-color: var(--danger); }
.tbtn.delbtn.on { background: #5a2020; border-color: var(--danger); color: #fff; }
.count.danger { color: var(--danger); }
.del-tag { background: #5a2020; color: #fff; border-radius: 10px;
  padding: 2px 10px; font-size: 11px; }
.card.armed { border-color: #5a2020; }
.card.armed:hover { border-color: var(--danger); }
.delbadge { background: rgba(90,32,32,.92); color: #fff;
  border: 1px solid var(--danger); }
.delbadge:hover { background: var(--danger); }
/* A protected card reads as protected before you touch it: the red delete
   badge becomes the same pink heart the mark uses, so the two are obviously
   the same fact rather than two unrelated icons. */
.delbadge.locked { background: rgba(20,20,32,.85); border-color: #f38ba8;
  color: #f38ba8; cursor: not-allowed; }
.delbadge.locked svg { fill: currentColor; }
.delbadge.locked:hover { background: rgba(243,139,168,.22); }
.clipbadge { background: rgba(20,20,32,.85); color: #fff;
  border: 1px solid var(--accent); }
.clipbadge:hover { background: var(--accent); }
.tbtn.clipbtn.on { background: var(--accent); border-color: var(--accent);
  color: #fff; }
.clip-tag { background: var(--accent); color: #fff; border-radius: 10px;
  padding: 2px 10px; font-size: 11px; }
.mode-tag {
  background: var(--accent); color: #fff; border-radius: 10px;
  padding: 2px 10px; font-size: 11px; margin-left: 8px;
}
.wbtn { background: none; border: 1px solid transparent; border-radius: 4px;
  color: var(--dim); cursor: pointer; padding: 2px 4px; display: inline-flex; }
.wbtn:hover { color: var(--accent); }
.wbtn.on { color: var(--accent); border-color: var(--accent); }
.wpanel { background: #1b1b2a; border: 1px solid #313148; border-radius: 6px;
  padding: 9px 12px; margin-bottom: 12px; font-size: 11px; max-width: 460px; }
.wh { color: var(--accent); margin-bottom: 6px; }
.wsrc { color: var(--dim); text-decoration: none; margin-left: 8px; }
.wsrc:hover { color: var(--accent); }
.wpanel table { border-collapse: collapse; }
.wpanel th { text-align: right; font-weight: normal; color: #8b8ba7;
  padding: 0 0 3px 18px; font-size: 10px; }
.wpanel th:first-child { padding-left: 0; }
.wpanel td { padding: 1px 0 1px 18px; color: var(--dim); }
.wpanel td:first-child { padding-left: 0; padding-right: 18px; }
.wpanel td.n { color: var(--text); text-align: right;
  font-variant-numeric: tabular-nums; }
.wpanel td.n.zero { color: #55556a; }
/* The numbers are buttons: clicking one filters the grid to those files.
   Styled as text so the table still reads as a table, underlined on hover so
   it is discoverable without a legend. */
.wpanel .num { background: none; border: none; padding: 0; font: inherit;
  color: var(--text); cursor: pointer; font-variant-numeric: tabular-nums; }
.wpanel .num:hover:not(:disabled) { color: var(--accent);
  text-decoration: underline; }
.wpanel .num.zero { color: #55556a; }
.wpanel .num:disabled { cursor: default; }
.tbtn.wchip { background: var(--accent); border-color: var(--accent);
  color: #fff; gap: 5px; }
.wpanel tr.tot td { border-top: 1px solid #313148; padding-top: 4px;
  color: var(--text); }
/* One blank row rather than a heading: the top half is "how the wiki files
   them" and the bottom half is "where the other files went", and each row
   already says which it is. */
.wpanel tr.gap td { height: 8px; padding: 0; }
.wpanel td.dash { color: #45455a; }
.wpanel .qual { color: var(--dim); font-size: 10px; margin-left: 4px; }
.wn { color: var(--dim); line-height: 1.8; margin-top: 7px; }
.wn b { color: var(--text); }
.wn .dim { opacity: .75; }
.msg { color: var(--dim); padding: 40px; text-align: center; }
.noterow { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.nlab { color: var(--dim); font-size: 11px; white-space: nowrap; }
.noterow input { flex: 1; font-size: 12px; padding: 5px 8px; }
/* fixed width: a date never needs more, and the title keeps the rest */
.noterow input.relin { flex: 0 0 104px; font-variant-numeric: tabular-nums; }
.noterow input.relin.bad { border-color: var(--danger, #e66); }
.relerr { color: var(--danger, #e66); font-size: 11px; margin: 4px 0 0 56px; }
/* Says how far this edit reaches. flex-shrink so it gives way to the input
   before the input does, and hidden on a phone where the row has no room. */
.thint { color: var(--dim); font-size: 10px; white-space: nowrap; flex-shrink: 0; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) { .thint { display: none; } }
.saved { color: var(--success); font-size: 12px; }
.fhdr { color: var(--accent); font-size: 13px; font-weight: bold;
  margin: 18px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #313148;
  display: flex; align-items: center; gap: 10px; }
.ftitle { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fcnt { color: var(--dim); font-weight: normal; font-size: 11px; }
.fbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; font-weight: normal; padding: 3px 10px;
  cursor: pointer; flex-shrink: 0; }
.fbtn:hover { border-color: var(--accent); }
.card { position: relative; }
.card.sel { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent) inset; }
.cbox { position: absolute; top: 6px; left: 6px; z-index: 2;
  background: rgba(0,0,0,.6); color: #fff; border-radius: 4px;
  padding: 0 5px; font-size: 15px; line-height: 1.4; }
.msg.err { color: var(--danger); }

.grid {
  display: grid;
  /* Columns flex to fill the width; the card itself is capped so a wide window
     gains columns instead of a few oversized cards, and the minimum keeps the
     usual card size close to that cap.
     The three numbers come from the group's chosen card shape (groupview.js) —
     they cannot be fixed here, because 350px-wide 4:5 cards would be 438px
     tall. The fallbacks are the 16:9 values, which is what every caller that
     does not pass a ratio (the inbox) still gets. */
  grid-template-columns:
    repeat(auto-fill, minmax(min(var(--card-min, 350px), 100%), 1fr));
  gap: 12px;
}
/* A phone on its side has about 690px left for cards (iOS reserves a strip
   on each side for the notch, plus the rail), just short of two 350px 16:9
   cards — so it showed one card the width of the screen. Capping the minimum
   at half the row guarantees two; narrower shapes still get more columns. */
@media (pointer: coarse) and (max-height: 500px) and (orientation: landscape) {
  .grid { grid-template-columns:
    repeat(auto-fill, minmax(min(var(--card-min, 350px), calc(50% - 6px)), 1fr)); }
}
.card {
  background: var(--card);
  border: 2px solid #404058;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: border-color .12s;
  max-width: var(--card-cap, 420px);   /* hard cap */
  width: 100%;
  margin: 0 auto;       /* centre inside a wider track */
}
.card:hover { border-color: var(--accent); }
.thumb { position: relative; aspect-ratio: var(--card-ratio, 16 / 9);
  background: #111122; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.meta { padding: 8px 10px; }
.thumb { position: relative; }
/* Corner buttons share one row. They used to be positioned individually on the
   same coordinates, so turning on delete mode covered the sheet button. */
.badges { position: absolute; top: 6px; right: 6px; z-index: 2;
  display: flex; align-items: center; gap: 4px; }
.tb { background: rgba(0,0,0,.6); border: 1px solid transparent; color: #fff;
  border-radius: 6px; padding: 4px 6px; cursor: pointer;
  display: inline-flex; align-items: center; }
/* only the sheet button is hover-revealed; the mode badges have to be visible
   the moment their mode is on, or the mode looks like it did nothing */
.sheetbtn { opacity: 0; transition: opacity .12s; }
.card:hover .sheetbtn { opacity: 1; }
.sheetbtn:hover { background: var(--accent); }
/* A phone has no hover, so the sheet button was all but unreachable there —
   it only flickered in while a finger happened to rest on the card. Always
   shown on a phone, and the corner buttons get a finger-sized target (the
   desktop's ~26px is fine for a mouse, not for a thumb). Written after the
   rules above: same specificity, so the later one wins. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .sheetbtn { opacity: 1; }
  .badges .tb { padding: 8px; border-radius: 8px; }
  /* Delete mode: the trash can goes to the opposite corner. Side by side with
     the now always-visible sheet button, a thumb aimed at one hit the other —
     and one of them deletes for good. The row spans the card and the trash
     can's auto margin pushes everything else right; without delete mode
     there is no trash can, so flex-end keeps the sheet button on the right. */
  .badges { left: 6px; justify-content: flex-end; }
  .delbadge { order: -1; margin-right: auto; }
}
/* Watched / favourite, bottom-right so they never share a corner with the
   action buttons — those come and go with a mode, these are just facts about
   the video and are visible all the time.
   `pointer-events: none` is the whole design decision: the card is one big
   "open this", and a tappable target sitting on top of it is a mis-tap waiting
   to happen on a phone. Changing a mark happens in the player, where there is
   room to mean it. */
.marks { position: absolute; bottom: 6px; right: 6px; z-index: 2;
  display: flex; align-items: center; gap: 5px; pointer-events: none;
  background: rgba(0,0,0,.55); border-radius: 6px; padding: 3px 6px; }
/* 共演: whose folder the video lives in. Bottom-left, opposite the marks. */
.leadtag { position: absolute; bottom: 6px; left: 6px; z-index: 2;
  max-width: calc(100% - 70px); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; pointer-events: none; font-size: 11px; color: #fff;
  background: rgba(0,0,0,.6); border-radius: 6px; padding: 2px 7px; }
/* the co-star row in the player */
.cochips { flex: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 5px;
  min-width: 0; }
.cochip { display: inline-flex; align-items: center; gap: 3px; font-size: 12px;
  background: var(--card); border: 1px solid #555; border-radius: 10px;
  padding: 1px 4px 1px 9px; }
.cochip.nm { border-style: dashed; color: #cfcfe0; }
.cochip button { background: none; border: none; color: var(--dim); cursor: pointer;
  padding: 0 3px; line-height: 0; }
.cochip button:hover { color: var(--text); }
/* the rest of the cast in the same row: plain text, the unmarked ones clickable */
.castname { font-size: 12px; color: var(--dim); background: none; border: none;
  padding: 1px 4px; border-radius: 10px; }
.castname.add { color: var(--accent); cursor: pointer; }
.castname.add:hover { background: #ffffff10; }
.castname.lead { color: #6c6c80; }
.castchar { color: var(--dim); font-size: 11px; }
/* 收錄: one line per related film, the whole line readable at a glance */
.relrow { align-items: flex-start; }
.rellist { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.relitem { display: flex; align-items: baseline; gap: 6px; font-size: 12px; min-width: 0; }
.relverb { color: var(--dim); flex-shrink: 0; }
.relid { background: none; border: none; padding: 0; font: inherit; flex-shrink: 0;
  color: #f9e2af; cursor: pointer; }
.relid.have { color: var(--success); }
.relid:hover { text-decoration: underline; }
.reltitle { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.relmeta { color: var(--dim); font-size: 11px; flex-shrink: 0; }
.relmore { align-self: flex-start; background: none; border: none; padding: 0;
  color: var(--accent); font-size: 11px; cursor: pointer; }
.noterow input.coin { flex: 1 1 120px; min-width: 120px; }
.mk.seen { color: #cfcfe0; }
/* The related number (relinfo.relLabel), bottom-left — the marks keep the
   right. Hover shows the summary; a click still opens the card. */
.reltag { position: absolute; bottom: 6px; left: 6px; z-index: 2;
  max-width: calc(100% - 70px); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; font-size: 11px; color: #89b4fa;
  background: rgba(0,0,0,.55); border-radius: 6px; padding: 2px 7px; }
.reltag.uptag { bottom: 30px; }
/* Lucide draws outlines; a filled heart is what reads as "favourite" at 13px.
   A CSS declaration beats the fill="none" presentation attribute on the svg.

   The 1px lift is optical, not a fix for a layout bug — the flex box centres
   the icon exactly (measured: 3px above, 3px below). Two things stack against
   it: Lucide's heart is drawn low in its own 24-unit box (centre 12.49, so
   0.53px low at this size), and a heart is top-heavy — wide lobes above, a
   point below — so geometric centring reads as sitting low anyway. 1px was
   chosen by eye from a side-by-side of 0 / 0.5 / 1 / 1.5px; there is no number
   that decides this one. */
.mk.fav { color: #f38ba8; fill: currentColor; transform: translateY(-1px); }

/* The filter buttons stay legible when a folder has none of that kind —
   dimmed rather than hidden, so the row does not change shape as you move
   between characters. */
.tbtn.markbtn.zero { color: var(--dim); border-color: #3a3a4a; }
.tbtn.markbtn .mcnt { color: var(--dim); font-size: 10px; margin-left: 3px; }
.tbtn.markbtn.on .mcnt { color: #fff; opacity: .8; }

.favbtn { background: none; border: none; color: var(--dim); cursor: pointer;
  padding: 4px 6px; display: inline-flex; align-items: center; flex-shrink: 0; }
.favbtn:hover { color: #f38ba8; }
.favbtn.on { color: #f38ba8; }
.favbtn.on svg { fill: currentColor; }

.name {
  font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* deliberately not .ftitle — that class is already the inbox folder heading
   further up this file */
/* One line, and the line is always there even when there is no title.
   Reserving it is the actual fix: the grid aligns each row to its tallest
   card, so a mix of titled and untitled cards left ragged rows and holes.
   That only became visible when the wiki import gave most films a title —
   before it, almost nothing had one and every card was the same height.
   The full text stays available on hover (`:title`) and in the player. */
.vtitle {
  font-size: 11px; color: var(--dim); margin-top: 3px; line-height: 1.4;
  height: 1.4em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sub { color: var(--dim); font-size: 11px; margin-top: 2px;
  display: flex; align-items: baseline; }
.sub .rel { margin-left: auto; font-variant-numeric: tabular-nums; }

.modal {
  position: fixed; inset: 0; background: rgba(0,0,0,.8);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.player-box {
  background: var(--panel); border-radius: 10px; padding: 12px;
  width: min(1100px, 92vw);
}
.player-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; gap: 12px;
}
.noterow .lnk { background: none; border: none; color: var(--dim);
  font-size: 11px; cursor: pointer; white-space: nowrap; }
.noterow .lnk:hover { color: var(--accent); }
.ptitle {
  font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  /* A flex item will not shrink past its content without this, so the ellipsis
     never engages and the title pushes on the button instead. */
  min-width: 0;
}
/* Measured on a 375px screen: the row had room (230 + 12 + 79 against 345) but
   the button was still allowed to shrink, so "關閉" wrapped onto two lines and
   the button grew from 28px tall to 54px. Nothing shows on a desktop width. */
.player-head .btn { flex-shrink: 0; white-space: nowrap; }
/* `.ib` puts a 6px gap between the icon and its label; with the label gone on
   a phone the gap would sit on its own inside the button. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) { .player-head .btn { gap: 0; } }
video { width: 100%; max-height: 74vh; max-height: 74dvh; background: #000; border-radius: 6px; display: block; }

/* ── phone: the video and nothing else ──────────────────────────────────── */
.modal.bare { background: #000; }
.modal.bare .player-box { width: 100vw; height: 100vh; height: 100dvh;
  padding: 0; border-radius: 0; background: none;
  display: flex; align-items: center; justify-content: center; }
.modal.bare :deep(.pwrap) { width: 100%; }
/* the keyboard-shortcut help sits in the corner the close button now uses,
   and a phone has no keyboard to take shortcuts from */
.modal.bare :deep(.help) { display: none; }
.modal.bare :deep(video) { max-height: 100vh; max-height: 100dvh;
  border-radius: 0; }
/* 12px in from the corner plus a 44px target: the corner itself is where the
   screen is rounded and where a sideways phone's notch or browser bar sits */
.barebtns { position: fixed; top: 12px; right: 12px; z-index: 51;
  display: flex; gap: 10px; }
.bbtn { width: 44px; height: 44px; border-radius: 22px; border: none;
  background: rgba(0,0,0,.55); color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center; padding: 0; }
.bbtn.on { color: #f38ba8; }
</style>
