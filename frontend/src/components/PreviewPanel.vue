<script setup>
import { ref, watch, computed, onMounted } from 'vue'
import { api, thumbUrl, streamUrl, errText } from '../api'
import { postWatched, postFav, favGuard, favGuardMessage } from '../videoflags'
import { store, can, setStatus } from '../store'
import { releaseFile } from '../player'
import VideoPlayer from './VideoPlayer.vue'
import Icon from './Icon.vue'

const props = defineProps({ video: { type: Object, required: true } })
const emit = defineEmits(['archive', 'queued', 'deleted', 'skip', 'close',
                          'marked'])

// preview frames across the whole video, like the desktop app
const PCTS = [0.05, 0.15, 0.25, 0.38, 0.50, 0.62, 0.75, 0.85, 0.92, 0.97]

const info = ref(null)
const person = ref('')
const chars = ref([])
const busy = ref(false)
const bigFrame = ref(null)
const playing = ref(false)
const note = ref('')
const noteSaved = ref(false)
const faceMatches = ref(null)   // null = not run, [] = no match
const faceBusy = ref(false)
const jav = ref(null)           // null = not run; { id, status, suggestions }
const javBusy = ref(false)
const doneMsg = ref('')         // brief ✔ shown in place before the panel closes

const canInfo = computed(() => store.editMode && can('edit_info'))
const canArchive = computed(() => store.editMode && can('archive'))

function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(1) + ' MB'
}
function fmtDur(s) {
  if (!s) return '--:--'
  const m = Math.floor(s / 60), r = Math.floor(s % 60)
  return `${m}:${String(r).padStart(2, '0')}`
}
function fmtSpeed(bps) {
  if (!bps) return ''
  return bps >= 1 << 20 ? (bps / (1 << 20)).toFixed(1) + ' MB/s'
                        : (bps / 1024).toFixed(0) + ' KB/s'
}

async function loadInfo() {
  info.value = null
  bigFrame.value = null
  playing.value = false
  note.value = ''
  noteSaved.value = false
  faceMatches.value = null
  jav.value = null
  // The listing already fetched these, so read them off the row rather than
  // making a request per selection while clicking through the inbox.
  fav.value = !!props.video.fav
  watched.value = !!props.video.watched
  try {
    info.value = await api(`/api/videoinfo?path=${encodeURIComponent(props.video.path)}`)
  } catch { /* ignore */ }
  try {
    const n = await api(`/api/note?path=${encodeURIComponent(props.video.path)}`)
    note.value = n.note || ''
  } catch { /* ignore */ }
}

async function runFaceMatch() {
  if (faceBusy.value) return
  faceBusy.value = true
  faceMatches.value = null
  try {
    const d = await api(`/api/face/match?path=${encodeURIComponent(props.video.path)}`)
    faceMatches.value = d.matches
    // auto-fill a confident suggestion when the field is still empty
    if (d.matches.length && d.matches[0].confidence >= 50 && !person.value.trim()) {
      person.value = d.matches[0].name
    }
  } catch (e) {
    faceMatches.value = []
    if (e.status !== 503) alert('辨識失敗：' + errText(e))
  } finally { faceBusy.value = false }
}

// Catalogue lookup: same shape as the face match above, so the two sets of
// suggestions can sit side by side and behave identically when clicked.
async function runJavLookup(refresh = false) {
  if (javBusy.value) return
  javBusy.value = true
  jav.value = null
  try {
    jav.value = await api(
      `/api/javdb/match?path=${encodeURIComponent(props.video.path)}` +
      (refresh ? '&refresh=1' : ''))
    const first = jav.value.suggestions?.[0]
    // only auto-fill when it resolves to a folder that already exists
    if (first?.known && !person.value.trim()) person.value = first.name
  } catch (e) {
    jav.value = { id: null, status: 'error', suggestions: [] }
    alert('番號查詢失敗：' + errText(e))
  } finally { javBusy.value = false }
}

const javNote = computed(() => {
  const j = jav.value
  if (!j) return ''
  return j.status === 'no_id'        ? '檔名中沒有可辨識的番號'
       : j.status === 'not_found'    ? `資料庫查無 ${j.id}`
       : j.status === 'no_performer' ? `${j.id} 有資料但未列出演員`
       : j.status === 'error'        ? '查詢失敗'
       : ''
})

// ── watched / favourite ────────────────────────────────────────────────────
// The panel owns its own copy rather than mutating `props.video`, because the
// inbox list rebuilds this component for each selection and the parent's row is
// what survives — so both are updated: the local one for this panel, and an
// event so the card behind it agrees.
const fav = ref(false)
const watched = ref(false)

async function onWatched() {
  if (watched.value) return
  watched.value = true
  try {
    await postWatched(api, props.video.path, true)
    emit('marked', { path: props.video.path, watched: true })
  } catch { watched.value = false }
}

async function toggleFav() {
  const next = !fav.value
  fav.value = next
  try {
    await postFav(api, props.video.path, next)
    emit('marked', { path: props.video.path, fav: next })
  } catch (e) {
    fav.value = !next
    setStatus('最愛儲存失敗：' + errText(e), 'warn')
  }
}

async function saveNote() {
  if (!canInfo.value) return
  try {
    await api('/api/note', {
      method: 'POST',
      body: JSON.stringify({ path: props.video.path, note: note.value }),
    })
    noteSaved.value = true
    setTimeout(() => { noteSaved.value = false }, 1500)
  } catch { /* ignore */ }
}

async function loadChars() {
  try {
    const d = await api('/api/characters')
    chars.value = d.characters.map(c => c.name)
  } catch { /* ignore */ }
}

function searchId() {
  if (!info.value?.video_id) return
  window.open('https://www.google.com/search?q=' +
    encodeURIComponent(info.value.video_id), '_blank', 'noopener')
}

async function doArchive() {
  const name = person.value.trim()
  if (!name) { alert('請先選擇或輸入角色名稱'); return }
  busy.value = true
  try {
    // pre-flight: duplicate + free space, like the desktop app
    const chk = await api('/api/archive/check', {
      method: 'POST',
      body: JSON.stringify({ path: props.video.path, person: name }),
    })
    if (!chk.fits) {
      alert(`目標磁碟空間不足\n需要 ${fmtSize(chk.size)}，剩餘 ${fmtSize(chk.free)}`)
      return
    }
    if (chk.duplicate &&
        !confirm(`「${name}」資料夾中疑似已有相同影片：\n\n${chk.duplicate}\n\n仍要繼續歸檔？`)) {
      return
    }
    // Hand the move to the app-level queue: the transfer keeps running (and is
    // reported in the status bar) even if you move on to the next video.
    doneMsg.value = `已加入佇列 →「${name}」`
    emit('archive', { video: props.video, person: name })
    await new Promise(r => setTimeout(r, 800))
    emit('queued')
  } catch (e) {
    alert('歸檔失敗：' + errText(e))
  } finally {
    busy.value = false
  }
}

async function doDelete() {
  // The heart is a few centimetres away in this panel, so say where it is.
  const guard = favGuard({ fav: fav.value, fav_others: props.video.fav_others },
                         store.user?.role)
  let force = false
  if (guard.kind === 'own' || (guard.kind === 'others' && !guard.canForce)) {
    alert(favGuardMessage(props.video.name, guard, '在備忘旁邊'))
    return
  }
  if (guard.kind === 'others') {
    // the admin path: this is the confirmation, so no second one follows
    if (!confirm(favGuardMessage(props.video.name, guard))) return
    force = true
  } else if (!confirm(`確定永久刪除？\n\n${props.video.name}`)) return
  try {
    // an open player holds the file, and Windows will not delete it then
    if (playing.value) await releaseFile(() => { playing.value = false })
    await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ path: props.video.path, force_fav: force }),
    })
    setStatus(`已刪除 ${props.video.name}`, 'ok')
    emit('deleted', props.video)
  } catch (e) {
    setStatus('刪除失敗', 'warn')
    alert('刪除失敗：' + errText(e))
  }
}

watch(() => props.video.path, loadInfo)
onMounted(() => { loadInfo(); loadChars() })
</script>

<template>
  <div class="panel">
    <div class="phead">
      <span class="fname" :title="video.name">{{ video.name }}</span>
      <button class="btn ghost sm" @click="emit('close')"><Icon name="close" :size="14" /></button>
    </div>

    <div class="cols">
      <!-- left: preview frames / player -->
      <div class="left">
        <div v-if="playing" class="playerbox">
          <VideoPlayer :src="streamUrl(video.path)" @close="playing = false"
                       @watched="onWatched" />
        </div>
        <div v-else>
          <div class="big" @click="playing = true">
            <img :src="bigFrame || thumbUrl(video.path, 0.5, 960)" />
            <span class="playhint ib"><Icon name="play" :size="18" /> 點擊播放</span>
          </div>
          <div class="frames">
            <img v-for="p in PCTS" :key="p" :src="thumbUrl(video.path, p, 320)"
                 loading="lazy" @mouseenter="bigFrame = thumbUrl(video.path, p, 960)" />
          </div>
        </div>
      </div>

      <!-- right: info + archive controls -->
      <div class="right">
        <div class="meta" v-if="info">
          <span>{{ fmtDur(info.duration) }}</span>
          <span>{{ info.width ? info.width + '×' + info.height : '?' }}</span>
          <span>{{ fmtSize(info.size) }}</span>
        </div>

        <div v-if="info?.video_id" class="vid">
          <Icon name="profile" :size="13" /> {{ info.video_id }}
          <button class="btn ghost sm ib" @click="searchId"><Icon name="search" :size="12" /> 查詢</button>
        </div>

        <label class="lab">
          備忘
          <span v-if="noteSaved" class="saved ib"><Icon name="check" :size="12" /> 已儲存</span>
          <span v-if="watched" class="seentag ib" title="播放超過 5 秒就會記錄">
            <Icon name="eye" :size="11" /> 看過</span>
          <!-- Deliberately not behind 編輯模式: these are the viewer's own
               record, not a change to the file. -->
          <button class="favbtn" :class="{ on: fav }"
                  :title="fav ? '取消我的最愛' : '加入我的最愛'"
                  @click="toggleFav">
            <Icon name="heart" :size="15" />
          </button>
        </label>
        <textarea v-model="note" rows="2" :readonly="!canInfo"
                  placeholder="輸入備忘（角色名、片商、標籤等）…"
                  @blur="saveNote"></textarea>

        <!-- wrapped in an element rather than left as a bare template so the
             mobile rule has something to hide; the memo above stays readable -->
        <div v-if="canArchive" class="archivetools no-mobile">
          <label class="lab">
            歸檔到角色
            <button v-if="store.faceAvailable" class="facebtn ib"
                    :disabled="faceBusy" @click="runFaceMatch">
              <Icon name="face" :size="12" /> {{ faceBusy ? '辨識中…' : '人臉辨識' }}
            </button>
            <button class="facebtn javbtn ib" :disabled="javBusy"
                    title="用檔名中的番號查 javdatabase"
                    @click="runJavLookup(false)">
              <Icon name="search" :size="12" /> {{ javBusy ? '查詢中…' : 'JAV DB' }}
            </button>
          </label>
          <div v-if="jav" class="matches">
            <template v-if="jav.suggestions?.length">
              <button v-for="sg in jav.suggestions" :key="sg.slug"
                      class="mchip javchip" :class="{ unknown: !sg.known }"
                      :title="sg.known ? `已有資料夾：${sg.name}`
                                       : '尚無此角色資料夾，點選後會以此名稱建立'"
                      @click="person = sg.name">
                {{ sg.name }}
                <span v-if="sg.romaji && sg.romaji !== sg.name" class="conf">{{ sg.romaji }}</span>
                <span v-if="!sg.known" class="newtag">新</span>
              </button>
              <span class="vidtag">{{ jav.id }}</span>
            </template>
            <span v-else class="nomatch">（{{ javNote }}）</span>
          </div>

          <div v-if="faceMatches" class="matches">
            <template v-if="faceMatches.length">
              <button v-for="m in faceMatches" :key="m.name" class="mchip"
                      @click="person = m.name">
                {{ m.name }} <span class="conf">{{ m.confidence }}%</span>
              </button>
            </template>
            <span v-else class="nomatch">（未找到符合的人）</span>
          </div>
          <input v-model="person" list="charlist" placeholder="選擇或輸入新角色名…" />
          <datalist id="charlist">
            <option v-for="c in chars" :key="c" :value="c" />
          </datalist>

          <div v-if="doneMsg" class="donebox ib"><Icon name="check" :size="15" /> {{ doneMsg }}</div>
          <div v-else class="actions">
            <button class="btn ib" :disabled="busy" @click="doArchive"><Icon name="check" :size="14" /> 歸檔</button>
            <button class="btn ghost ib" :disabled="busy" @click="emit('skip')"><Icon name="skip" :size="14" /> 跳過</button>
            <button class="btn del ib" :disabled="busy" @click="doDelete"
                    title="永久刪除這部影片"><Icon name="trash" :size="14" /> 刪除</button>
          </div>
        </div>
        <div v-else class="ro">切換到 🔧 編輯模式即可歸檔</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { background: var(--panel); border-top: 2px solid var(--accent);
  padding: 10px 14px; }
.phead { display: flex; justify-content: space-between; align-items: center;
  gap: 12px; margin-bottom: 8px; }
.fname { font-size: 13px; font-weight: bold; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.cols { display: flex; gap: 16px; }
.left { flex: 1; min-width: 0; }
.right { width: 280px; flex-shrink: 0; }
.big { position: relative; cursor: pointer; }
.big img { width: 100%; max-height: 240px; object-fit: contain;
  background: #111; border-radius: 6px; display: block; }
.playhint { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; color: #fff; font-size: 15px; opacity: 0;
  background: rgba(0,0,0,.35); border-radius: 6px; transition: opacity .15s; }
.big:hover .playhint { opacity: 1; }
.frames { display: flex; gap: 4px; margin-top: 6px; overflow-x: auto; }
.frames img { width: 84px; height: 48px; object-fit: cover; border-radius: 3px;
  background: #111; cursor: pointer; border: 2px solid transparent; }
.frames img:hover { border-color: var(--accent); }
.meta { display: flex; gap: 12px; color: var(--dim); font-size: 11px; }
.vid { margin-top: 8px; font-size: 12px; color: #f9e2af;
  display: flex; align-items: center; gap: 8px; }
.lab { display: block; color: var(--dim); font-size: 10px; margin: 12px 0 4px; }
.right input, .right textarea { width: 100%; font-family: inherit; resize: vertical; }
.saved { color: var(--success); font-size: 10px; margin-left: 6px; }
.seentag { color: var(--dim); font-size: 10px; margin-left: 6px; }
/* Floated to the end of the label so the heart sits at the right-hand edge of
   the memo field, the same place it occupies in the player. */
.favbtn { float: right; background: none; border: none; color: var(--dim);
  cursor: pointer; padding: 0 2px; display: inline-flex; align-items: center; }
.favbtn:hover { color: #f38ba8; }
.favbtn.on { color: #f38ba8; }
.favbtn.on svg { fill: currentColor; }
.facebtn { background: none; border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 10px; padding: 2px 8px; margin-left: 8px;
  cursor: pointer; }
.facebtn:hover { border-color: var(--accent); }
.facebtn:disabled { color: var(--dim); }
.matches { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
.mchip { background: #1a3a1a; border: 1px solid var(--success);
  color: var(--success); border-radius: 10px; padding: 2px 10px;
  font-size: 11px; cursor: pointer; }
.mchip:hover { background: #2a5a2a; }
.conf { opacity: .75; font-size: 10px; }
.nomatch { color: var(--dim); font-size: 11px; }
.actions { display: flex; gap: 6px; margin-top: 10px; }
.donebox { margin-top: 10px; padding: 9px 12px; border-radius: 6px;
  background: #162416; border: 1px solid var(--success);
  color: var(--success); font-size: 13px; text-align: center; }
.btn.sm { padding: 2px 8px; font-size: 11px; }
.btn.del { background: #5a2020; color: var(--danger); }
.javbtn { margin-left: 6px; }
.mchip.javchip { border-color: #89b4fa; }
.mchip.javchip.unknown { border-style: dashed; color: var(--dim); }
.newtag { background: #89b4fa; color: #11111b; border-radius: 7px;
  padding: 0 5px; font-size: 9px; margin-left: 4px; }
.vidtag { color: var(--dim); font-size: 10px; align-self: center; }
.prog { margin-top: 10px; }
.bar { height: 6px; background: var(--card); border-radius: 3px; overflow: hidden; }
.fill { height: 100%; background: var(--accent); transition: width .2s; }
.ptxt { color: var(--dim); font-size: 10px; margin-top: 3px; }
.ro { color: var(--dim); font-size: 11px; margin-top: 14px; }
.playerbox { max-height: 300px; }
</style>
