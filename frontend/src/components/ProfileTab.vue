<script setup>
import { ref, watch, onMounted, computed, nextTick } from 'vue'
import { api, thumbUrl, streamUrl, imageUrl, imageThumbUrl, errText } from '../api'
import { store, can, setStatus } from '../store'
import { releaseFile } from '../player'
import MediaViewer from './MediaViewer.vue'
import AvatarCropDialog from './AvatarCropDialog.vue'
import { avatarImgStyle } from '../avatar'
import { groupData } from '../groups'
import { viewForChar, usesFolderPoster } from '../groupview'
import { profileCells, FIELD_LABELS, FIELD_ORDER } from '../profilefields'
import { linkChips, linkColor } from '../links'
import { suggest, apply as applyTag, moveIndex } from '../tagsuggest'
import { toggle as pickToggle, selectAll, targets, chunk,
         applyResult, resultMessage } from '../photobatch'
import { sortMedia, nextSort } from '../photosort'
import { flatItems, buildAlbums, albumsApply, sortAlbums, nextAlbumSort,
         albumCaption } from '../albums'
import Icon from './Icon.vue'
import SimilarDialog from './SimilarDialog.vue'

const props = defineProps({ folder: { type: String, required: true } })

// `links` arrives split out by the server; the file on the NAS still holds one
// combined list. See media.split_tags for why the field held 37 URLs and 7
// actual tags before this.
const info = ref({ aliases: [], tags: [], links: [], notes: '',
                   profile_photo: '', profile: null })

// formatting lives in ../profilefields so the JAV個資查詢 preview agrees
const cells = computed(() => profileCells(info.value.profile))
const hasProfile = computed(() => cells.value.length > 0)
const mediaItems = ref([])
const loading = ref(true)
const editing = ref(false)
const showViewer = ref(false)
const viewerIdx = ref(0)

// edit fields (comma-separated strings)
const aliasStr = ref('')
const tagStr = ref('')
const linkStr = ref('')
const profStr = ref({})

// ── tags ───────────────────────────────────────────────────────────────────
// Every tag already used anywhere in the library, for the editor's
// autocomplete. It comes from /api/characters, which the sidebar has loaded
// already — no second endpoint and no cache to keep in step.
//
// This is the half that was missing. The survey found 44 tags used 44 times:
// not one was ever reused, because there was no way to see what you had used
// before and no reason to reuse it if you had.
const vocab = ref([])

// site + handle per link, so 星川優子's two Instagram accounts stop rendering
// as two identical chips. Only 2 of 627 characters have that collision, but
// the user chose to show the handle everywhere rather than only on a clash.
const chips = computed(() => linkChips(info.value.links))

async function loadVocab() {
  try {
    const d = await api('/api/characters')
    const seen = new Map()
    for (const c of d.characters) {
      for (const t of c.tags || []) seen.set(t, (seen.get(t) || 0) + 1)
    }
    // most used first: the whole point is to reach for one you already have
    vocab.value = [...seen.entries()].sort((a, b) => b[1] - a[1] ||
                                                     a[0].localeCompare(b[0]))
  } catch { /* autocomplete is a convenience; never block the page for it */ }
}

// ── the suggestion dropdown ────────────────────────────────────────────────
// Not a <datalist>: that matches against the whole value of the input, and
// this field holds a comma-separated list — so it worked for the first tag and
// silently did nothing for every one after it. The matching logic is in
// ../tagsuggest.js with tests, because "no suggestions appeared" and "there
// was nothing to suggest" look identical from the outside.
const tagInput = ref(null)
const caret = ref(0)
const sugOpen = ref(false)
const sugIdx = ref(-1)

const suggestions = computed(() =>
  sugOpen.value ? suggest(vocab.value, tagStr.value, caret.value) : [])

function syncCaret() {
  caret.value = tagInput.value?.selectionStart ?? tagStr.value.length
}

function onTagInput() {
  syncCaret()
  sugOpen.value = true
  sugIdx.value = -1
}

function pick(tag) {
  const r = applyTag(tagStr.value, caret.value, tag)
  tagStr.value = r.value
  sugIdx.value = -1
  nextTick(() => {
    const el = tagInput.value
    if (el) { el.focus(); el.setSelectionRange(r.caret, r.caret); caret.value = r.caret }
  })
}

function onTagKey(e) {
  const n = suggestions.value.length
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!sugOpen.value) { sugOpen.value = true; return }
    e.preventDefault()
    sugIdx.value = moveIndex(sugIdx.value, e.key === 'ArrowDown' ? 1 : -1, n)
  } else if ((e.key === 'Enter' || e.key === 'Tab') && sugIdx.value >= 0) {
    // Only when a row is highlighted: Enter with nothing chosen must stay
    // free to submit whatever you actually typed.
    e.preventDefault()
    pick(suggestions.value[sugIdx.value].tag)
  } else if (e.key === 'Escape') {
    sugOpen.value = false
  }
}

// mousedown, not click: blur fires first and would close the list before the
// click landed, so picking with the mouse would do nothing
function onSugDown(e, tag) {
  e.preventDefault()
  pick(tag)
}

// Clicking a tag asks "who else has this?" — the answer is the home grid,
// which is the only place that shows characters side by side.
function browseTag(t) {
  store.tagFilter = t
  store.selectedChar = null
  store.view = 'home'
}


const canEdit = computed(() => store.user?.can_edit && store.editMode)
// One per permission group this page touches (see roles.py): editing the
// profile, the person/similar tools, and delete mode are separate ticks.
const canInfo = computed(() => store.editMode && can('edit_info'))
const canPerson = computed(() => store.editMode && can('person'))
const canDel = computed(() => store.editMode && can('delete'))

// The folder cover, and whether this character's group wants it used. Only one
// of the two settings reaches here: a round home tile means "no folder cover",
// so the header keeps the person icon and the two views stay consistent.
const folderPoster = ref(null)
const charView = computed(() =>
  viewForChar(store.selectedChar?.name, groupData.groups, groupData.view))
const charShape = computed(() => charView.value.home)

const avatarUrl = computed(() => {
  if (info.value.profile_photo) {
    // profile_photo is relative to the folder
    return imageUrl(props.folder + '/' + info.value.profile_photo)
  }
  // Folder cover, when this character's group asks for it. Without this a
  // character whose folder obviously has a picture still shows a person icon
  // the moment you click through from a home card that was showing it.
  if (folderPoster.value && usesFolderPoster(charShape.value)) {
    return imageUrl(folderPoster.value)
  }
  return null
})

// A folder cover is not the avatar: it has no stored framing, and the crop
// editor writes its result onto `profile_photo`, which is not set here. So the
// header is only clickable when there is a real 大頭照 to reframe.
const canCrop = computed(() => canInfo.value && !!info.value.profile_photo)

// framing chosen in the cropper — header, home grid and editor share this
const avNatW = ref(0)
const avNatH = ref(0)

function onAvatarLoad(e) {
  avNatW.value = e.target.naturalWidth
  avNatH.value = e.target.naturalHeight
}

const avatarStyle = computed(() => {
  // plain centred cover for a folder poster — nothing to frame
  if (!info.value.profile_photo) return {}
  return avatarImgStyle(avNatW.value, avNatH.value,
                        info.value.profile_photo_pos || { x: 50, y: 50, zoom: 1 })
})

const showCrop = ref(false)

async function saveCrop(pos) {
  showCrop.value = false
  try {
    const d = await api(`/api/profile/photo?folder=${encodeURIComponent(props.folder)}`,
      { method: 'POST', body: JSON.stringify({ pos }) })
    info.value = { ...info.value, profile_photo_pos: d.profile_photo_pos }
  } catch (e) { alert('儲存失敗：' + errText(e)) }
}

async function load() {
  loading.value = true
  // belongs to the character being left, not the one arriving
  noPersonMode.value = false
  // every character starts where its group says; the 相簿/全部照片 switch is
  // not remembered (the user's choice — the group setting is the answer to
  // "how should this kind of character be browsed")
  pageMode.value = 'albums'
  openAlbum.value = null
  albumSort.value = { by: 'name', dir: 'asc' }
  similarGroups.value = null
  similarThreshold.value = null
  person.value = { ready: false, noPerson: [], pending: 0, total: 0 }
  try {
    const d = await api(`/api/profile?folder=${encodeURIComponent(props.folder)}`)
    info.value = d.info
    folderPoster.value = d.poster || null
    mediaItems.value = d.media
    similarThreshold.value = d.similar_threshold ?? null
  } catch { /* ignore */ } finally {
    loading.value = false
  }
  if (sort.value.by === 'similar') {
    // A remembered 相似 on a folder nobody has tuned would be a sort whose
    // button is hidden in browse mode — no way to see why the order is what
    // it is, and no way out. Fall back to time instead.
    if (similarThreshold.value == null) sort.value = { by: 'newest', dir: 'desc' }
    else loadSimilarGroups()
  }
  // after the grid, not before: cached verdicts are cheap but this walks the
  // folder, and the photos should appear first
  loadPersonFlags()
}

function startEdit() {
  aliasStr.value = info.value.aliases.join(', ')
  tagStr.value = info.value.tags.join(', ')
  // one per line, not comma-separated: URLs are long, and a row of them in a
  // single-line input is unreadable and unclickable to edit
  linkStr.value = (info.value.links || []).join('\n')
  loadVocab()
  const p = info.value.profile || {}
  profStr.value = Object.fromEntries(FIELD_ORDER.map(k => [k, p[k] || '']))
  editing.value = true
}

function clearProfile() {
  if (!confirm('清除這位角色的「個人資料」欄位？\n\n只會清掉這一區，別名與標籤不受影響。')) return
  for (const k of FIELD_ORDER) profStr.value[k] = ''
  save()
}

async function save() {
  const body = {
    aliases: aliasStr.value.split(',').map(s => s.trim()).filter(Boolean),
    tags: tagStr.value.split(',').map(s => s.trim()).filter(Boolean),
    // Always sent, even when empty — the server treats a missing `links` as
    // "old client, leave them alone", which would make clearing the last link
    // impossible.
    links: linkStr.value.split(/[\n,]/).map(s => s.trim()).filter(Boolean),
    // 備註 no longer has an editor (only 2 of 613 characters ever used it,
    // and the one real entry was hand-copied profile data this tool now
    // fetches). Preserved verbatim so saving never silently drops it.
    notes: info.value.notes || '',
    profile: Object.fromEntries(
      FIELD_ORDER.map(k => [k, (profStr.value[k] || '').trim()])),
  }
  try {
    const d = await api(`/api/profile?folder=${encodeURIComponent(props.folder)}`,
      { method: 'POST', body: JSON.stringify(body) })
    info.value = d.info
    // folderPoster is deliberately not touched: the save response does not
    // carry it, and editing 個資 fields cannot change what is in the folder.
    editing.value = false
  } catch (e) { alert('儲存失敗：' + errText(e)) }
}

// ── delete mode (shared with the videos tab via the store) ─────────────────
async function doDelete(m) {
  const kind = m.is_video ? '影片' : '照片'
  if (!confirm(`確定永久刪除這個${kind}？此操作無法復原。\n\n${m.name || m.path}`)) return
  try {
    // a video open in the viewer holds the file, and Windows will not delete
    // it then; photos are read once and released, so they need no such care
    if (showViewer.value && m.is_video) {
      await releaseFile(() => { showViewer.value = false })
    }
    await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ path: m.path }),
    })
    mediaItems.value = mediaItems.value.filter(x => x.path !== m.path)
    // the verdict list still names it, and the filtered grid reads
    // from that list
    person.value = { ...person.value,
      noPerson: person.value.noPerson.filter(x => x !== m.path) }
    // the avatar pointed at the file we just removed
    if (info.value.profile_photo &&
        m.path.endsWith(info.value.profile_photo.replace(/\//g, '\\'))) {
      info.value.profile_photo = ''
    }
    setStatus(`已刪除 ${m.name || m.path}`, 'warn')
  } catch (e) {
    setStatus('刪除失敗：' + errText(e), 'warn')
    alert('刪除失敗：' + errText(e))
  }
}

async function setAvatar(item) {
  try {
    await api(`/api/profile/photo?folder=${encodeURIComponent(props.folder)}`,
      { method: 'POST', body: JSON.stringify({ photo_path: item.path }) })
    await load()
    showCrop.value = true      // like Instagram: choose the framing right away
  } catch (e) { alert('設定失敗：' + errText(e)) }
}

/* ── person filter ───────────────────────────────────────────────────────────
 * Flags photos the detector found nobody in, so scenery and food shots can be
 * picked out of a social-media dump. Measured on one character: 204 of 1,136
 * photos (18%), 72 s on the GPU, and 0.1 s the next time because the verdicts
 * are cached.
 *
 * It only ever filters. Deleting stays in the existing delete mode — one photo
 * at a time, each confirmed — because the verdict comes from a model that
 * nobody has checked. In an earlier measurement a face detector was wrong
 * about this same question 81% of the time; this detector is much better
 * suited, but "much better" is not "verified", and the whole reason to show
 * these in the grid is so you look at them.
 */
const person = ref({ ready: false, noPerson: [], pending: 0, total: 0 })
const personAvailable = ref(false)
// why it is unavailable, if it is. An absent button explains nothing — the
// same reason `Not Found` is translated to 「後端需要重啟」 in api.js.
const personReason = ref('')
// true when the packages and model are there but CUDA is not: it still works,
// about 4.75x slower and using the whole CPU, and the user should know before
// starting rather than after
const personSlow = ref(false)

// Detection threshold. Server-clamped; these are only the slider's bounds.
const conf = ref(0.35)
const confRange = ref({ min: 0.20, max: 0.60, def: 0.35 })
// the threshold the cached verdicts were produced at — moving the slider away
// from it means the numbers on screen no longer describe what you would get
const scannedConf = ref(null)
const confChanged = computed(() =>
  scannedConf.value !== null && Math.abs(conf.value - scannedConf.value) > 1e-9)
const scanningPerson = ref(false)
const personProgress = ref('')
const personError = ref('')
const noPersonMode = ref(false)

const noPersonSet = computed(() => new Set(person.value.noPerson))
/* ── sorting: 最近新增 / 名稱 / 大小 / 相似 ────────────────────────────────────

   Same buttons and click behaviour as the video grid. Remembered in its own
   localStorage key — which sort suits a photo grid and which suits a video
   list are separate choices.

   相似 is the user's two-mode design: in edit mode the button opens the
   settings dialog every time; in browse mode it only exists once an editor
   has applied it to this folder, and then it simply sorts with that threshold.
   The threshold lives on the server so a phone or another account gets the
   same order. See core/photosimilar.py for the numbers behind it.  */

const LS_SORT = 'vam.profile.sort'
const sort = ref((() => {
  try {
    const v = JSON.parse(localStorage.getItem(LS_SORT) || 'null')
    if (v && ['newest', 'name', 'size', 'similar'].includes(v.by)) return v
  } catch { /* fall through */ }
  return { by: 'newest', dir: 'desc' }
})())
watch(sort, v => {
  try { localStorage.setItem(LS_SORT, JSON.stringify(v)) } catch { /* ignore */ }
}, { deep: true })

const similarThreshold = ref(null)      // null = never applied to this folder
const similarGroups = ref(null)         // { path: groupId } once loaded
const showSimilar = ref(false)
const similarVisible = computed(() => canPerson.value || similarThreshold.value != null)

async function loadSimilarGroups() {
  const folder = props.folder
  try {
    const d = await api(`/api/profile/similar?folder=${encodeURIComponent(folder)}`)
    if (folder !== props.folder) return        // switched character meanwhile
    similarGroups.value = d.groups || {}
  } catch (e) {
    setStatus('相似排序讀取失敗：' + errText(e), 'warn')
  }
}

function setSort(by) {
  if (by === 'similar' && canPerson.value) { showSimilar.value = true; return }
  sort.value = nextSort(sort.value, by)
  if (by === 'similar' && similarGroups.value == null) loadSimilarGroups()
}

function onSimilarApplied({ threshold, groups }) {
  similarThreshold.value = threshold
  similarGroups.value = groups || {}
  sort.value = sort.value.by === 'similar' ? sort.value : { by: 'similar', dir: 'desc' }
  showSimilar.value = false
  setStatus(`相似排序已套用（門檻 ${threshold}）`, 'ok')
}

// The flat grid: set videos dropped, so it is exactly what it was before the
// album view existed. See albums.flatItems.
const flat = computed(() => flatItems(mediaItems.value))

const sortedMedia = computed(() =>
  sortMedia(flat.value, sort.value.by, sort.value.dir, similarGroups.value))

const shownMedia = computed(() => noPersonMode.value
  ? sortedMedia.value.filter(m => !m.is_video && noPersonSet.value.has(m.path))
  : sortedMedia.value)

async function loadPersonFlags() {
  try {
    const d = await api(
      `/api/person/flags?folder=${encodeURIComponent(props.folder)}`)
    // `ready` means "something has been scanned here", not "all of it" —
    // `pending` carries the rest, so the count never implies more than it knows
    person.value = { ready: d.total > d.pending, noPerson: d.no_person,
                     pending: d.pending, total: d.total }
    if (d.conf_min != null) {
      confRange.value = { min: d.conf_min, max: d.conf_max, def: d.conf_default }
    }
    scannedConf.value = d.scanned_conf ?? null
    // start the slider where this folder was actually scanned, so the first
    // thing you see agrees with the numbers beside it
    conf.value = d.scanned_conf ?? d.conf_default ?? 0.35
    if (!person.value.noPerson.length) noPersonMode.value = false
  } catch { /* a folder that was never scanned is not an error */ }
}

async function scanPerson() {
  personError.value = ''
  scanningPerson.value = true
  personProgress.value = '啟動中…'
  try {
    const { job_id } = await api('/api/person/scan', {
      method: 'POST',
      body: JSON.stringify({ folder: props.folder, conf: conf.value }),
    })
    while (true) {
      await new Promise(r => setTimeout(r, 700))
      const j = await api(`/api/person/job/${job_id}`)
      const p = j.progress
      if (p && p.total) personProgress.value = `辨識中 ${p.done} / ${p.total}`
      if (j.status === 'done' || j.status === 'cancelled') break
      if (j.status === 'error') { personError.value = j.error || '辨識失敗'; break }
    }
    await loadPersonFlags()
  } catch (e) {
    personError.value = '人物辨識失敗：' + errText(e)
  } finally {
    scanningPerson.value = false
    personProgress.value = ''
  }
}

/* ── batch delete, only inside the person filter ─────────────────────────────
 * Deleting 204 photos one confirmation at a time is not review, it is
 * attrition. But "no batch delete, one at a time, each confirmed" was a
 * deliberate rule, so it is replaced by guards rather than simply dropped:
 *
 *   * ticking only exists while the 沒有人物 filter is on, so a batch can
 *     never reach a photo the filter did not put in front of you;
 *   * nothing starts selected — 全選 is one click, and a default of
 *     "everything armed" is one stray click away from deleting the lot;
 *   * clicking a card still opens the viewer, because the thumbnail is not
 *     enough to judge a false positive;
 *   * the confirmation states the number and that it cannot be undone (there
 *     is no recycle bin in this app);
 *   * failures are pinned to the cards they belong to, not thrown away in an
 *     alert — the usual cause is transient and the retry should be targeted.
 */
const picked = ref(new Set())
const deleting = ref(false)
const delProgress = ref('')
const delErrors = ref({})          // path -> message

const batchMode = computed(() => noPersonMode.value && store.deleteMode)

function togglePick(m) { picked.value = pickToggle(picked.value, m.path) }
function pickAll(on) { picked.value = selectAll(shownMedia.value, on) }

// leaving either mode disarms everything; a selection that outlives the screen
// it was made on is a trap
watch([noPersonMode, () => store.deleteMode], () => {
  picked.value = new Set()
  delErrors.value = {}
})

async function deletePicked() {
  // the intersection, not just what is ticked: see photobatch.targets
  const paths = targets(shownMedia.value, picked.value)
  if (!paths.length) return
  if (!confirm(`永久刪除選取的 ${paths.length} 張照片？\n\n` +
               `此操作無法復原，不會進資源回收筒。`)) return
  deleting.value = true
  delErrors.value = {}
  const gone = new Set()
  try {
    // in chunks so the progress moves and one slow file cannot stall the lot;
    // the endpoint retries a busy file for ~4.5 s before giving up
    let done = 0
    for (const batch of chunk(paths, 25)) {
      done += batch.length
      delProgress.value = `刪除中 ${done} / ${paths.length}`
      const r = await api('/api/media/delete', {
        method: 'POST', body: JSON.stringify({ paths: batch }),
      })
      for (const p of r.deleted || []) gone.add(p)
      for (const e of r.errors || []) delErrors.value[e.path] = e.error
    }
  } catch (e) {
    alert('刪除失敗：' + errText(e))
  } finally {
    deleting.value = false
    delProgress.value = ''
  }
  const r = applyResult(
    { items: mediaItems.value, noPerson: person.value.noPerson,
      picked: picked.value },
    [...gone],
    Object.entries(delErrors.value).map(([path, error]) => ({ path, error })))
  mediaItems.value = r.items
  person.value = { ...person.value, noPerson: r.noPerson }
  picked.value = r.picked
  setStatus(resultMessage(r.deletedCount, r.failedCount),
            r.failedCount ? 'warn' : 'ok')
}

/* ── albums: one card per subfolder (groupview `albums`) ──────────────────────
 * Photo-set characters keep one set per subfolder; the flat grid mixes all of
 * them into one sheet. Grouping is by the `album` field the server tags every
 * file with, so it costs no extra I/O. The rules are in ../albums.js.
 *
 * The 相似 sort and the 沒有人物 filter are tools across the whole folder, so
 * they live in 全部照片 only. */
const pageMode = ref('albums')           // 'albums' | 'all'
const openAlbum = ref(null)              // album key, or null for the card list
const albumSort = ref({ by: 'name', dir: 'asc' })
// inside a set, name order is the order it was shot in (001, 002…); time is
// usually one mtime for the whole set and would give an arbitrary order
const innerSort = ref({ by: 'name', dir: 'asc' })
const mcountEl = ref(null)

const albums = computed(() => buildAlbums(mediaItems.value))
const albumsOn = computed(() => albumsApply(charView.value.albums, albums.value))
const inAlbums = computed(() => albumsOn.value && pageMode.value === 'albums')
const sortedAlbums = computed(() =>
  sortAlbums(albums.value, albumSort.value.by, albumSort.value.dir))
// null when the open album was emptied by deleting — back to the card list
const curAlbum = computed(() => inAlbums.value && openAlbum.value !== null
  ? albums.value.find(a => a.key === openAlbum.value) || null : null)
const albumMedia = computed(() => curAlbum.value
  ? sortMedia(curAlbum.value.items, innerSort.value.by, innerSort.value.dir) : [])

// what the media grid shows: one album, or the flat grid (possibly filtered)
const gridItems = computed(() => inAlbums.value ? albumMedia.value : shownMedia.value)
// what the viewer pages through: inside an album it stays inside that album
const viewerItems = computed(() => inAlbums.value ? albumMedia.value : sortedMedia.value)

function setPageMode(m) {
  if (m === pageMode.value) return
  pageMode.value = m
  openAlbum.value = null
  noPersonMode.value = false
}

function enterAlbum(a) {
  openAlbum.value = a.key
  innerSort.value = { by: 'name', dir: 'asc' }
  // the card may have been far down the list; start the set at its top
  nextTick(() => mcountEl.value?.scrollIntoView({ block: 'start' }))
}

function leaveAlbum() {
  const key = openAlbum.value
  openAlbum.value = null
  // back to the card you came from, not the top of a 51-card list
  nextTick(() => {
    const el = document.querySelector(`[data-album="${CSS.escape(key ?? '')}"]`)
    el?.scrollIntoView({ block: 'center' })
  })
}

function setAlbumSort(by) { albumSort.value = nextAlbumSort(albumSort.value, by) }
function setInnerSort(by) { innerSort.value = nextSort(innerSort.value, by) }

function albumThumb(a) {
  if (!a.cover) return ''
  return a.cover.is_video ? thumbUrl(a.cover.path) : imageThumbUrl(a.cover.path)
}

// open the unified viewer at this item; photos and videos browse together
function openItem(item) {
  // the viewer browses in the order on screen, so it indexes that list
  viewerIdx.value = viewerItems.value.findIndex(m => m.path === item.path)
  showViewer.value = viewerIdx.value >= 0
}

// platform brand colours for tag chips

watch(() => props.folder, load)
onMounted(async () => {
  load()
  // whether the scan button appears at all; a machine without the
  // model or the packages should not offer a button that 503s
  try {
    const p = await api('/api/person/status')
    personAvailable.value = !!(p.deps && p.model)
    personReason.value = p.reason || ''
    personSlow.value = !!(p.deps && p.model && !p.gpu)
  } catch (e) {
    personAvailable.value = false
    personReason.value = '無法取得人物偵測狀態：' + errText(e)
  }
})
</script>

<template>
  <div class="profile">
    <!-- header -->
    <div class="phead">
      <div class="avatar" :class="{ editable: canCrop }"
           :title="canCrop ? '點擊調整大頭照位置' : ''"
           @click="canCrop && (showCrop = true)">
        <img v-if="avatarUrl" :src="avatarUrl"
             :class="{ plain: !info.profile_photo }"
             :style="avatarStyle" @load="onAvatarLoad" />
        <Icon v-else name="user" :size="52" class="ph" />
        <span v-if="canCrop" class="avedit ib"><Icon name="pencil" :size="12" /> 調整</span>
      </div>
      <div class="pinfo">
        <div class="pname">{{ store.selectedChar?.name }}</div>

        <template v-if="!editing">
          <div v-if="info.aliases.length" class="aliases">
            {{ info.aliases.join('  ·  ') }}
          </div>
          <!-- Two rows now, because the one field held two unrelated things:
               37 URLs and 7 actual tags across the whole library. Links keep
               exactly the rendering they had. -->
          <div v-if="chips.length" class="tags">
            <a v-for="c in chips" :key="c.url" class="chip link"
               :href="c.url" target="_blank" rel="noopener noreferrer"
               :title="c.title" :style="{ background: linkColor(c.key) }"
               @click.stop>{{ c.text }}<Icon name="link" :size="11" /></a>
          </div>
          <div v-if="info.tags.length" class="tags">
            <!-- Clickable, which is the whole change: a tag that cannot answer
                 "who else has this" is decoration, and the survey showed that
                 is exactly what these had become — 44 tags, none reused. -->
            <button v-for="t in info.tags" :key="t" class="chip tagbtn"
                    :title="`看看還有誰標了「${t}」`"
                    @click.stop="browseTag(t)">{{ t }}</button>
          </div>
          <!-- filled from javdatabase by the JAV個資查詢 tool; kept apart from the
               user's own tags so it stays obvious which is which -->
          <!-- a spec-sheet grid: one wrapping row of "label value" pairs
               was hard to scan, and these are exactly the fields people
               compare between characters -->
          <div v-if="hasProfile" class="prof">
            <div class="stats">
              <div v-for="c in cells" :key="c.key" class="stat">
                <div class="k">{{ c.label }}</div>
                <div class="v">{{ c.value }}<span v-if="c.note"
                  class="note">{{ c.note }}</span></div>
              </div>
            </div>
          </div>
          <button v-if="canInfo" class="editbtn ib no-mobile" @click="startEdit"><Icon name="pencil" :size="13" /> 編輯資料</button>
        </template>

        <template v-else>
          <div class="field"><label>別名</label>
            <input v-model="aliasStr" placeholder="逗號分隔" /></div>
          <div class="field tagfield"><label>標籤</label>
            <input ref="tagInput" v-model="tagStr" placeholder="逗號分隔"
                   autocomplete="off"
                   @input="onTagInput" @keydown="onTagKey"
                   @click="syncCaret" @keyup="syncCaret"
                   @focus="sugOpen = true" @blur="sugOpen = false" />
            <!-- The list is the whole feature: 44 tags existed and not one was
                 ever reused, because there was no way to see what you had
                 already used. -->
            <ul v-if="suggestions.length" class="sug">
              <li v-for="(s, i) in suggestions" :key="s.tag"
                  :class="{ on: i === sugIdx }"
                  @mousedown="e => onSugDown(e, s.tag)"
                  @mouseenter="sugIdx = i">
                <span class="st">{{ s.tag }}</span>
                <span class="sn">{{ s.count }} 位</span>
              </li>
            </ul>
          </div>
          <div class="field"><label>連結</label>
            <textarea v-model="linkStr" rows="3"
                      placeholder="一行一個網址&#10;https://www.instagram.com/xxx/"></textarea>
          </div>
          <div class="fsep">個人資料<span class="fhint2">（可手動修改；再次執行「JAV個資查詢」時，已有內容的欄位一律跳過）</span></div>
          <div class="pgrid">
            <div v-for="k in FIELD_ORDER" :key="k" class="field">
              <label>{{ FIELD_LABELS[k] }}</label>
              <input v-model="profStr[k]"
                     :placeholder="k === 'dob' ? '2000-01-31，只知年份可寫 2000-??-??' : ''" />
            </div>
          </div>
          <div class="erow">
            <button class="btn ib" @click="save"><Icon name="check" :size="14" /> 儲存</button>
            <button class="btn ghost" @click="editing = false">取消</button>
            <span class="spacer"></span>
            <button v-if="hasProfile" class="btn ghost danger" @click="clearProfile">
              清除個人資料
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- media grid -->
    <div ref="mcountEl" class="mcount" :class="{ danger: store.deleteMode }">
      <!-- inside an album: the way back comes first, where the eye lands -->
      <template v-if="curAlbum">
        <button class="backbtn ib" @click="leaveAlbum">
          <Icon name="chevronLeft" :size="13" /> 全部相簿</button>
        <span class="aname" :title="curAlbum.name">{{ curAlbum.name }}</span>
        <span class="acnt">{{ albumCaption(curAlbum) }}</span>
      </template>
      <span v-else-if="inAlbums" class="ib"><Icon name="camera" :size="13" />
        {{ albums.length }} 本相簿 · {{ flat.length }} 張照片</span>
      <span v-else class="ib"><Icon name="camera" :size="13" /> {{ shownMedia.length }} 個媒體<template
        v-if="noPersonMode">（共 {{ flat.length }}）</template></span>
      <span v-if="albumsOn && !curAlbum" class="tgroup fgroup">
        <button class="tbtn" :class="{ on: pageMode === 'albums' }"
                @click="setPageMode('albums')">相簿</button>
        <button class="tbtn" :class="{ on: pageMode === 'all' }"
                @click="setPageMode('all')">全部照片</button>
      </span>
      <!-- phone only: ends the first line (count + 相簿/全部照片) -->
      <span class="brk"></span>
      <span v-if="store.deleteMode" class="del-tag ib">
        <Icon name="warn" :size="12" /> 刪除模式 — 點垃圾桶會永久刪除</span>
      <span class="spacer"></span>

      <!-- Editor tools as one group, so on a phone they share a line instead
           of being scattered between the count and the sort buttons. -->
      <span v-if="canPerson || canDel" class="tgroup egroup">
      <!-- Person filter. The count is the point: without it "沒有人物" is a
           leap of faith, and an empty result reads as broken rather than as
           "there are none". Same lesson as the 未看過 filter. -->
      <template v-if="canPerson && person.ready && !inAlbums">
        <!-- `|| noPersonMode` is the whole point: delete the last person-free
             photo and the count becomes 0, and a button that disappears at
             that moment leaves the filter stuck on with no way out — an empty
             grid and no explanation. Same rule the video filter carries. -->
        <button v-if="person.noPerson.length || noPersonMode" class="pbtn no-mobile"
                :class="{ on: noPersonMode }"
                @click="noPersonMode = !noPersonMode"
                title="模型判斷這些照片裡沒有人物 —— 請自己看過再刪">
          <Icon name="user" :size="12" /> 沒有人物 {{ person.noPerson.length }}
        </button>
        <span v-else class="pnone no-mobile">沒有人物 0</span>
        <span v-if="person.pending" class="ppend no-mobile" :title="`${person.pending} 張還沒判斷過`">
          {{ person.pending }} 張未判斷
        </span>
      </template>
      <button v-if="canPerson && personAvailable && !scanningPerson && !inAlbums"
              class="pbtn no-mobile" :class="{ on: confChanged }" @click="scanPerson">
        <Icon name="search" :size="12" />
        {{ confChanged ? '用新門檻重新辨識'
                       : (person.ready ? '重新辨識' : '辨識人物') }}
      </button>
      <span v-if="scanningPerson" class="ppend ib no-mobile">
        <Icon name="spinner" :size="12" class="spin" /> {{ personProgress }}
      </span>
      <!-- Nothing at all is the worst answer to "why is there no button". -->
      <span v-if="canPerson && !personAvailable && personReason && !inAlbums" class="pnone no-mobile"
            :title="personReason">人物辨識不可用</span>

      <button v-if="canDel && mediaItems.length" class="delbtn"
              :class="{ on: store.deleteMode }"
              @click="store.deleteMode = !store.deleteMode">
        <Icon name="trash" :size="12" />
        {{ store.deleteMode ? '結束刪除' : '刪除模式' }}
      </button>
      </span>
      <!-- Sort last in the row, the same order the video grid uses. The album
           list and the inside of an album sort on their own, unremembered. -->
      <span v-if="inAlbums && !curAlbum" class="tgroup sgroup">
      <span class="slab">排序</span>
      <button v-for="s in [['name', '名稱'], ['newest', '最近新增'], ['count', '數量']]"
              :key="s[0]" class="tbtn ib" :class="{ on: albumSort.by === s[0] }"
              @click="setAlbumSort(s[0])">
        {{ s[1] }}
        <Icon v-if="albumSort.by === s[0]" :name="albumSort.dir === 'asc' ? 'up' : 'down'" :size="11" />
      </button>
      </span>
      <span v-else-if="curAlbum && curAlbum.items.length > 1" class="tgroup sgroup">
      <span class="slab">排序</span>
      <button v-for="s in [['name', '名稱'], ['newest', '最近新增'], ['size', '大小']]"
              :key="s[0]" class="tbtn ib" :class="{ on: innerSort.by === s[0] }"
              @click="setInnerSort(s[0])">
        {{ s[1] }}
        <Icon v-if="innerSort.by === s[0]" :name="innerSort.dir === 'asc' ? 'up' : 'down'" :size="11" />
      </button>
      </span>
      <span v-else-if="!inAlbums && flat.length > 1" class="tgroup sgroup">
      <span class="slab">排序</span>
      <button v-for="s in [['newest', '最近新增'], ['name', '名稱'], ['size', '大小']]"
              :key="s[0]" class="tbtn ib" :class="{ on: sort.by === s[0] }"
              @click="setSort(s[0])">
        {{ s[1] }}
        <Icon v-if="sort.by === s[0]" :name="sort.dir === 'asc' ? 'up' : 'down'" :size="11" />
      </button>
      <button v-if="similarVisible" class="tbtn ib" :class="{ on: sort.by === 'similar' }"
              :title="canPerson ? '設定相似門檻並掃描' : `相似照片排在一起（門檻 ${similarThreshold}）`"
              @click="setSort('similar')">
        相似
        <template v-if="sort.by === 'similar'">
          <Icon v-if="canPerson" name="settings" :size="11" />
          <Icon v-else :name="sort.dir === 'asc' ? 'up' : 'down'" :size="11" />
        </template>
      </button>
      <!-- In edit mode the 相似 button opens the dialog, so flipping direction
           needs its own control while that sort is active. -->
      <button v-if="canPerson && sort.by === 'similar'" class="tbtn ib"
              :title="sort.dir === 'asc' ? '目前舊到新' : '目前新到舊'"
              @click="sort = { by: 'similar', dir: sort.dir === 'asc' ? 'desc' : 'asc' }">
        <Icon :name="sort.dir === 'asc' ? 'up' : 'down'" :size="11" />
        {{ sort.dir === 'asc' ? '舊到新' : '新到舊' }}
      </button>
      </span>
    </div>
    <SimilarDialog v-if="showSimilar" :folder="folder"
                   @close="showSimilar = false" @applied="onSimilarApplied" />

    <!-- Selection bar. Only while the person filter AND delete mode are both
         on: a batch must never be able to reach a photo the filter did not
         put in front of you. -->
    <div v-if="batchMode" class="pickbar">
      <span class="ib"><Icon name="checked" :size="13" />
        已選 <b>{{ picked.size }}</b> / {{ shownMedia.length }}</span>
      <button class="lnk" @click="pickAll(true)">全選</button>
      <button class="lnk" @click="pickAll(false)">全不選</button>
      <span class="spacer"></span>
      <span v-if="deleting" class="ppend ib">
        <Icon name="spinner" :size="12" class="spin" /> {{ delProgress }}</span>
      <button class="btn danger" :disabled="!picked.size || deleting"
              @click="deletePicked">
        <Icon name="trash" :size="13" /> 刪除已選 {{ picked.size }} 張
      </button>
    </div>
    <div v-if="batchMode" class="pickhint">
      點卡片會開大圖確認；點左上角的方框才是勾選。刪除無法復原。
    </div>

    <!-- The threshold.
         Only after a scan: the first run uses the default, and you adjust once
         you have a result to react to. Offering the dial before there is
         anything to tune is a question with no context.

         The wording avoids "嚴謹" entirely — it is ambiguous here. A high
         threshold is strict about *detecting a person*, which makes it loose
         about *flagging photos*: exactly backwards from how people read it. So
         both ends are labelled by their outcome instead. -->
    <div v-if="canPerson && personAvailable && (person.ready || scanningPerson) && !inAlbums"
         class="confrow">
      <span class="spacer"></span>
      <button v-if="conf !== confRange.def" class="lnk"
              @click="conf = confRange.def">回到預設 {{ confRange.def }}</button>
      <label class="ib">辨識門檻
        <span class="cend">標記較少</span>
        <input type="range" :min="confRange.min" :max="confRange.max" step="0.05"
               v-model.number="conf" :disabled="scanningPerson" />
        <span class="cend">標記較多</span>
        <b>{{ conf.toFixed(2) }}</b>
      </label>
      <!-- No explanatory paragraph, and no "these numbers were computed at X"
           line. The two things they said are carried by the controls
           themselves: the track is labelled 標記較少 / 標記較多 at its ends,
           and the scan button turns into 用新門檻重新辨識 the moment the
           slider leaves what the cache holds. A warning attached to the thing
           you would act on beats a sentence on its own row. -->
    </div>

    <!-- It still works without CUDA, just slowly and using the whole CPU.
         Saying so before the scan beats discovering it 25 minutes in. -->
    <div v-if="canPerson && personSlow" class="msg warn">
      {{ personReason }}
    </div>
    <div v-if="canPerson && !personAvailable && personReason" class="msg warn">
      {{ personReason }}
    </div>
    <div v-if="personError" class="msg err">{{ personError }}</div>
    <div v-if="loading" class="msg">載入中…</div>
    <!-- A second way out, right where you are looking. The empty grid is the
         normal end of this job — you deleted them all — so it should read as
         success with a door, not as a dead end. -->
    <div v-else-if="noPersonMode && !shownMedia.length" class="msg empty-np">
      <span>{{ person.total ? '沒有剩下任何「沒有人物」的照片了。'
                            : '這個角色沒有被判定為「沒有人物」的照片。' }}</span>
      <button class="btn" @click="noPersonMode = false">顯示全部照片</button>
    </div>
    <!-- the album cards -->
    <div v-else-if="inAlbums && !curAlbum" class="agrid">
      <div v-for="a in sortedAlbums" :key="a.key" class="acard"
           :data-album="a.key" @click="enterAlbum(a)">
        <div class="acover">
          <img v-if="a.cover" :src="albumThumb(a)" loading="lazy" />
          <span v-if="a.videos" class="badge vcount ib">
            <Icon name="play" :size="11" /> {{ a.videos }}</span>
        </div>
        <div class="atitle" :title="a.name">{{ a.name }}</div>
        <div class="asub">{{ albumCaption(a) }}</div>
      </div>
    </div>
    <div v-else class="mgrid">
      <div v-for="m in gridItems" :key="m.path" class="mcard"
           :class="{ armed: store.deleteMode, picked: picked.has(m.path),
                     failed: !!delErrors[m.path] }" @click="openItem(m)">
        <img :src="m.is_video ? thumbUrl(m.path) : imageThumbUrl(m.path)" loading="lazy" />
        <span v-if="m.is_video" class="badge"><Icon name="play" :size="12" /></span>
        <!-- a real target, not a 13px icon: this gets tapped 200 times in a row -->
        <button v-if="batchMode" class="pick" @click.stop="togglePick(m)"
                :title="picked.has(m.path) ? '取消勾選' : '勾選要刪除'">
          <Icon :name="picked.has(m.path) ? 'checked' : 'unchecked'" :size="18" />
        </button>
        <button v-if="canInfo && !m.is_video && !store.deleteMode" class="setav"
                @click.stop="setAvatar(m)" title="設為大頭照"><Icon name="star" :size="13" /></button>
        <button v-if="store.deleteMode" class="delbadge" title="永久刪除"
                @click.stop="doDelete(m)"><Icon name="trash" :size="14" /></button>
        <!-- pinned to the card, not thrown into an alert: the reason matters
             and the retry should be aimed at these specific files -->
        <span v-if="delErrors[m.path]" class="cerr" :title="delErrors[m.path]">
          {{ delErrors[m.path] }}</span>
      </div>
    </div>

    <!-- avatar crop editor (Instagram-style: the user chooses the framing) -->
    <AvatarCropDialog
      v-if="showCrop && canCrop"
      :src="avatarUrl"
      :initial="info.profile_photo_pos"
      @save="saveCrop"
      @close="showCrop = false"
    />

    <!-- unified viewer: photos and IG videos in one continuous list -->
    <MediaViewer
      v-if="showViewer"
      :items="viewerItems"
      :start="viewerIdx"
      @close="showViewer = false"
    />
  </div>
</template>

<style scoped>
.profile { padding: 16px; }
.phead { display: flex; gap: 24px; padding: 8px 8px 20px; border-bottom: 1px solid #313148; }
.avatar {
  width: 170px; height: 170px; border-radius: 85px; overflow: hidden; flex-shrink: 0;
  background: var(--card); display: flex; align-items: center; justify-content: center;
  border: 3px solid #404058;
}
.avatar { position: relative; }
/* size/offset come from avatarImgStyle() — same maths as the crop editor */
.avatar img { display: block; }
/* a folder cover has no stored framing */
.avatar img.plain { position: absolute; inset: 0;
  width: 100%; height: 100%; object-fit: cover; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  /* No hover on a touch screen, so a hint that only appears on hover never
     appears at all — the avatar would be tappable with nothing to say so. */
  .avatar.editable .avedit { opacity: 1; }
}
.avatar .ph { font-size: 54px; color: var(--dim); }
.avatar.editable { cursor: pointer; }
.avatar.editable:hover { border-color: var(--accent); }
.avedit {
  position: absolute; left: 0; right: 0; bottom: 0; text-align: center;
  background: rgba(0,0,0,.55); color: #fff; font-size: 11px; padding: 5px 0;
  opacity: 0; transition: opacity .15s; pointer-events: none;
}
.avatar.editable:hover .avedit { opacity: 1; }
.pinfo { flex: 1; }
.pname { font-size: 22px; font-weight: bold; }
.aliases { color: #a6adc8; font-size: 13px; margin-top: 6px; }
.tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.chip { color: #fff; border-radius: 9px; padding: 3px 11px; font-size: 11px;
  font-weight: bold; display: inline-block; }
.chip.link { text-decoration: none; cursor: pointer; }
.chip.link:hover { filter: brightness(1.25); }
/* Tags read as outlined rather than solid, so the two rows are obviously two
   kinds of thing — links are a place to go, tags are a way to group. */
.chip.tagbtn { background: transparent; border: 1px solid var(--accent);
  color: var(--accent); font: inherit; font-size: 11px; font-weight: bold;
  cursor: pointer; padding: 2px 10px; }
.chip.tagbtn:hover { background: var(--accent); color: #fff; }
.field textarea { width: 100%; font-family: inherit; font-size: 12px;
  resize: vertical; }

/* The suggestion list floats over what follows rather than pushing it down:
   the profile grid is right underneath, and a field that shoves six rows of
   inputs around while you type is worse than no suggestions at all. */
.tagfield { position: relative; }
.sug { position: absolute; z-index: 30; left: 0; right: 0; top: 100%;
  margin: 2px 0 0; padding: 4px 0; list-style: none;
  background: #1b1b2a; border: 1px solid #3a3a52; border-radius: 6px;
  box-shadow: 0 6px 18px rgba(0,0,0,.45);
  max-height: 208px; overflow-y: auto; }
.sug li { display: flex; align-items: center; gap: 10px; cursor: pointer;
  padding: 5px 10px; font-size: 12px; color: var(--text); }
.sug li.on { background: var(--accent); color: #fff; }
.sug .st { flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
/* how many characters already carry it — the number is the reason to reuse
   one instead of inventing a near-duplicate */
.sug .sn { color: var(--dim); font-size: 10px; flex-shrink: 0; }
.sug li.on .sn { color: #fff; opacity: .75; }
.fhint { color: var(--dim); font-size: 10px; margin-top: 3px; }
.prof { margin-top: 12px; }
/* Each cell is as wide as what it holds. This was a grid of equal
   `minmax(96px, 1fr)` tracks, and `auto-fill` opens an invisible track every
   ~102px of container width, snapping every cell back to the 96px floor —
   but "1996-08-15" needs 98.9px and "B88 / W58 / H85" needs 131px. Measured
   over 200-1200px: 188 of the 1001 possible widths wrapped the birthday, in
   bands that grow wider as the window does (4px wide near 200px, 32px near
   1116px). Sizing to content removes the floor, and the row then matches the
   tag chips directly above it. */
.stats { display: flex; flex-wrap: wrap; gap: 6px; }
.stat { background: #1b1b2a; border: 1px solid #262636; border-radius: 8px;
  padding: 6px 9px; flex: 0 1 auto;
  min-width: 76px; }        /* so 罩杯 "F" is a cell, not a sliver */
.stat .k { color: var(--dim); font-size: 10px; letter-spacing: .04em; }
.stat .v { color: var(--text); font-size: 14px; font-weight: 600;
  margin-top: 2px; white-space: nowrap; }
/* The age beside the birthday. Smaller, dimmer and lighter than the date, so
   the date stays the value and this reads as an annotation on it — inside the
   value it was as loud as the date itself. */
.stat .v .note { color: var(--dim); font-size: 11px; font-weight: 500;
  margin-left: 7px; }
.fsep { margin: 14px 0 6px; font-size: 12px; color: var(--dim);
  border-bottom: 1px solid #262636; padding-bottom: 4px; }
.fhint2 { font-size: 10px; margin-left: 6px; }
.pgrid { display: grid; gap: 8px 12px;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
.pgrid .wide { grid-column: 1 / -1; }
.erow .spacer { flex: 1; }
.btn.danger { color: #e88; }
.editbtn {
  background: none; border: none; color: var(--dim); font-size: 11px;
  margin-top: 10px; padding: 0; cursor: pointer;
}
.editbtn:hover { color: var(--text); }
.field { margin-top: 8px; }
.field label { display: block; color: var(--dim); font-size: 10px; margin-bottom: 3px; }
.field input, .field textarea { width: 100%; }
.erow { margin-top: 12px; display: flex; gap: 8px; }

.mcount { display: flex; align-items: center; gap: 8px;
  color: #a6adc8; font-size: 12px; font-weight: bold; padding: 12px 4px 8px; }
.mcount .spacer { flex: 1; }
.mcount { flex-wrap: wrap; }
.mcount .slab { color: var(--dim); font-size: 11px; font-weight: normal; margin-left: 8px; }
.mcount .tbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; font-weight: normal; padding: 3px 10px;
  cursor: pointer; }
.mcount .tbtn:hover { border-color: var(--accent); }
.mcount .tbtn.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.mcount.danger { color: var(--danger); }
/* Same grouping as the video grid's toolbar: a label and its buttons wrap as
   one unit, and on a phone each group takes its own line and scrolls sideways
   rather than breaking apart. */
.mcount .tgroup { display: inline-flex; align-items: center; gap: 8px; flex-wrap: nowrap; }
.mcount button, .mcount .pnone, .mcount .ppend { white-space: nowrap; }
.mcount .brk { display: none; }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .mcount { padding: 10px 6px 8px; row-gap: 8px; }
  .mcount .spacer { display: none; }
  /* Same two-line layout as the video grid (the user's):
       📷 n 本相簿 · n 張照片            [相簿][全部照片]
       [刪除模式]            排序 [名稱][最近新增][數量]
     The person tools (沒有人物 / 辨識人物) are hidden on a phone, so the
     editor group is just the delete button, kept left like the video grid. */
  /* grow into what the line has left, buttons pushed right — see the note
     on the same rule in VideoGrid.vue */
  .mcount .tgroup { flex: 1 1 0; min-width: 0; overflow-x: auto; scrollbar-width: none; }
  .mcount .tgroup::before { content: ''; margin-left: auto; }
  .mcount > .ib:first-child { flex-shrink: 0; white-space: nowrap; }
  .mcount .tgroup::-webkit-scrollbar { display: none; }
  .mcount .tgroup .slab { margin-left: 0; }
  .mcount .fgroup { order: 1; }
  .mcount .brk { display: block; order: 2; flex-basis: 100%; height: 0;
    margin-top: -8px; }   /* its own zero-height line: cancel one row-gap */
  .mcount .del-tag { order: 3; flex-basis: 100%; }
  .mcount .egroup { order: 4; flex: 0 0 auto; }
  .mcount .egroup::before { display: none; }
  .mcount .sgroup { order: 5; }
}
.del-tag { background: #5a2020; color: #fff; border-radius: 10px;
  padding: 2px 10px; font-size: 11px; font-weight: normal; }
.empty-np { display: flex; align-items: center; justify-content: center;
  gap: 12px; flex-wrap: wrap; }

/* The threshold row. */
.confrow { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 6px; font-size: 12px; color: var(--dim); }
.confrow input[type=range] { width: 150px; accent-color: var(--accent); }
/* Labelling both ends of the track is the fix for "which way is stricter":
   the number alone never answered it. */
.cend { color: var(--dim); font-size: 10px; }
/* `.spacer` only carries `flex: 1` under .erow and .mcount, so the one in
   this row did nothing and the slider stayed on the left. */
.confrow .spacer { flex: 1; }
.confrow b { color: var(--text); font-variant-numeric: tabular-nums; }
.confrow .lnk { background: none; border: none; color: var(--accent);
  font-size: 11px; cursor: pointer; }
.msg.warn { color: #f9e2af; }

/* Batch selection, only shown inside the person filter. */
.pickbar { display: flex; align-items: center; gap: 10px; margin-top: 8px;
  padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
  font-size: 12px; color: var(--dim); }
.pickbar b { color: var(--accent); font-variant-numeric: tabular-nums; }
.pickbar .lnk { background: none; border: none; color: var(--accent);
  font-size: 12px; cursor: pointer; }
.pickhint { color: var(--dim); font-size: 11px; margin: 4px 2px 0; }
.btn.danger { color: #e88; border-color: #e8888844; }
.btn.danger:disabled { opacity: .45; cursor: default; }
/* The tick target. 18px icon in a 34px box because this gets tapped a couple
   of hundred times in a row, and a miss opens the viewer instead. */
.mcard .pick { position: absolute; top: 4px; left: 4px; z-index: 2;
  width: 34px; height: 34px; display: flex; align-items: center;
  justify-content: center; border: none; border-radius: 8px;
  background: rgba(0,0,0,.55); color: #fff; cursor: pointer; padding: 0; }
.mcard .pick:hover { background: rgba(0,0,0,.75); }
.mcard.picked { outline: 2px solid var(--accent); outline-offset: -2px; }
.mcard.picked img { opacity: .55; }
.mcard.failed { outline: 2px solid #e88; outline-offset: -2px; }
.mcard .cerr { position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
  background: rgba(0,0,0,.8); color: #e88; font-size: 10px; padding: 3px 5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Person filter. `pbtn` matches `delbtn`'s shape so the row reads as one
   group of controls rather than as a bolted-on feature. */
.pbtn { background: none; border: 1px solid var(--line); color: var(--dim);
  border-radius: 6px; padding: 3px 9px; font-size: 11px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 4px; }
.pbtn:hover { color: var(--text); border-color: var(--accent); }
.pbtn.on { background: var(--accent); border-color: var(--accent); color: #fff; }
/* Shown rather than hidden when the answer is zero: a filter that vanishes
   looks like it failed to run. */
.pnone { color: var(--dim); font-size: 11px; }
.ppend { color: #f9e2af; font-size: 11px; }
.msg.err { color: var(--danger, #e66); }
.delbtn { display: inline-flex; align-items: center; gap: 6px;
  background: var(--card); border: 1px solid #5a2020; border-radius: 4px;
  color: var(--danger); font-size: 11px; font-weight: normal;
  padding: 3px 10px; cursor: pointer; }
.delbtn:hover { border-color: var(--danger); }
.delbtn.on { background: #5a2020; border-color: var(--danger); color: #fff; }
.mcard.armed { outline: 2px solid #5a2020; outline-offset: -2px; }
.delbadge { position: absolute; top: 6px; right: 6px; z-index: 2;
  background: rgba(90,32,32,.92); color: #fff; border: 1px solid var(--danger);
  border-radius: 5px; padding: 4px 7px; cursor: pointer;
  display: inline-flex; align-items: center; }
.delbadge:hover { background: var(--danger); }
.mgrid {
  /* Flexible columns capped at 5: the grid can never be wider than five
     280px tiles plus their gaps, and the 236px minimum stops a sixth column
     from squeezing in at that width. */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(236px, 100%), 1fr));
  gap: 3px;
  max-width: calc(5 * 280px + 4 * 3px);   /* 1412px */
  margin: 0 auto;
}
/* On a phone the desktop rules left a 31px margin down each side: only one
   column fits, and `.mcard`'s 280px cap (there to stop tiles growing huge on
   a wide screen) then centred it inside a 343px column. Three columns is the
   familiar photo-grid shape and puts ~15 photos on screen instead of 1.6.
   At 375px that is 120px per tile — the cached thumbnails are 560px on the
   short edge, so they stay sharp even at 3x. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .mgrid { grid-template-columns: repeat(3, 1fr); }
  .mcard { max-width: none; }
  /* the picture grid goes edge to edge; the profile header above it keeps
     its breathing room via .phead's own padding */
  .profile { padding: 16px 4px; }
}
.mcard {
  position: relative; aspect-ratio: 4 / 5; background: var(--card);
  overflow: hidden; cursor: pointer;        /* square corners, IG-style */
  max-width: 280px; width: 100%; margin: 0 auto;
}
.mcard img { width: 100%; height: 100%; object-fit: cover; display: block; }
.badge {
  position: absolute; top: 6px; right: 6px; width: 22px; height: 22px;
  background: rgba(0,0,0,.55); color: #fff; border-radius: 11px;
  display: flex; align-items: center; justify-content: center; font-size: 11px;
}
.setav {
  position: absolute; bottom: 6px; right: 6px; border: none; cursor: pointer;
  background: rgba(0,0,0,.55); color: #f9e2af; border-radius: 6px; padding: 3px 6px;
}
/* Must come after the rule above: same specificity (one class each), so this
   only wins by being later. 3px of padding round a 13px icon is a 19px target;
   a fingertip needs about 44. */
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .setav { padding: 11px 13px; bottom: 4px; right: 4px; }
}
.msg { color: var(--dim); padding: 30px; text-align: center; }

/* ── album view ── */
.backbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; font-weight: normal; padding: 3px 10px;
  cursor: pointer; flex-shrink: 0; }
.backbtn:hover { border-color: var(--accent); }
/* set names run long ("Yeha 예하, [PURE MEDIA] Vol.210 누드 디지털화보 Set.01") */
.aname { color: var(--text); min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.acnt { color: var(--dim); font-weight: normal; white-space: nowrap; }
/* Same column rules as the photo grid, so a card is the size of a photo tile. */
.agrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(200px, 100%), 1fr));
  gap: 14px 10px;
  max-width: calc(6 * 240px + 5 * 10px);
  margin: 0 auto;
}
.acard { cursor: pointer; min-width: 0; }
.acover { position: relative; aspect-ratio: 4 / 5; background: var(--card);
  overflow: hidden; border-radius: 6px; }
.acover img { width: 100%; height: 100%; object-fit: cover; display: block;
  transition: transform .15s; }
.acard:hover .acover img { transform: scale(1.03); }
.acard:hover .atitle { color: var(--accent); }
.badge.vcount { width: auto; padding: 0 7px; gap: 3px; }
/* two lines, then an ellipsis: long enough to tell Set.01 from Set.02 */
.atitle { margin-top: 6px; font-size: 12px; color: var(--text); line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-word; }
.asub { margin-top: 2px; font-size: 11px; color: var(--dim); }
@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .agrid { grid-template-columns: repeat(2, 1fr); gap: 12px 6px; padding: 0 4px; }
}

/* the viewer lives in MediaViewer.vue now */
</style>
