<script setup>
import { onMounted, ref, computed, watch } from 'vue'
import { api, errText } from './api'
import { store, can, setStatus, setBusy, logArchived, resetUserState } from './store'
import { followScan } from './convertscan'
import { clearCharStats } from './charstats'
import { groupData } from './groups'
import { viewForChar, pickTab } from './groupview'
import { resumeScan } from './dedup'
import { resumeScan as resumeImgScan } from './imgdedup'
import LoginView from './components/LoginView.vue'
import CharacterSidebar from './components/CharacterSidebar.vue'
import VideoGrid from './components/VideoGrid.vue'
import ProfileTab from './components/ProfileTab.vue'
import DupDialog from './components/DupDialog.vue'
import JavDialog from './components/JavDialog.vue'
import ConvertDialog from './components/ConvertDialog.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import HomeView from './components/HomeView.vue'
import GroupManagerDialog from './components/GroupManagerDialog.vue'
import PreviewPanel from './components/PreviewPanel.vue'
import MergeDialog from './components/MergeDialog.vue'
import StatusBar from './components/StatusBar.vue'
import FolderArchiveDialog from './components/FolderArchiveDialog.vue'
import Icon from './components/Icon.vue'
import PasswordDialog from './components/PasswordDialog.vue'
import TwoFactorDialog from './components/TwoFactorDialog.vue'
import SessionsDialog from './components/SessionsDialog.vue'
import InterruptedJobsDialog from './components/InterruptedJobsDialog.vue'
import { followJob } from './jobfollow'

const ready = ref(false)
const selectedInbox = ref(null)
const inboxGrid = ref(null)
const mergeChar = ref(null)
const faceIndexing = ref(false)

async function runFaceIndex() {
  if (!confirm('為所有角色建立人臉索引？\n\n第一次執行會逐一分析每部影片，可能需要很長時間。\n（已索引過的影片會自動跳過）')) return
  faceIndexing.value = true
  try {
    const { job_id } = await api('/api/face/index', {
      method: 'POST', body: JSON.stringify({}),
    })
    while (true) {
      await new Promise(r => setTimeout(r, 800))
      const j = await api(`/api/face/job/${job_id}`)
      if (j.progress && typeof j.progress === 'object') {
        setBusy(`人臉索引：${j.progress.person} / ${j.progress.file}`)
      }
      if (j.status === 'done') {
        setStatus(`人臉索引完成，新增 ${j.result.indexed} 部`, 'ok'); break
      }
      if (j.status === 'error') { setStatus('索引失敗：' + j.error, 'warn'); break }
    }
  } catch (e) {
    setStatus('索引失敗：' + errText(e), 'warn')
  } finally { faceIndexing.value = false; setBusy('') }
}

function onMergeDone() {
  mergeChar.value = null
  store.selectedChar = null
  sidebarKey.value++
  setStatus('資料夾整合完成', 'ok')
}

const showLog = ref(false)
const folderToArchive = ref(null)
const batchSel = ref([])
const batchPerson = ref('')
const selMode = ref(false)
const charNames = ref([])

async function loadCharNames() {
  try {
    const d = await api('/api/characters')
    charNames.value = d.characters.map(c => c.name)
  } catch { /* ignore */ }
}

function toggleSelMode() {
  if (!charNames.value.length) loadCharNames()
  inboxGrid.value?.toggleSelectMode()
  selMode.value = !selMode.value
  if (!selMode.value) { batchSel.value = []; batchPerson.value = '' }
}

const batchBytes = computed(() =>
  batchSel.value.reduce((n, v) => n + (v.size || 0), 0))

function fmtSize(b) {
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + ' GB'
  return (b / (1 << 20)).toFixed(1) + ' MB'
}

function archiveSelected() {
  const person = batchPerson.value.trim()
  if (!person) { alert('請輸入或選擇角色名稱'); return }
  if (!confirm(`將 ${batchSel.value.length} 部影片歸檔到「${person}」？`)) return
  for (const v of batchSel.value) enqueueArchive({ video: v, person })
  inboxGrid.value?.clearSel()
  batchSel.value = []
}

function onFolderArchived() {
  folderToArchive.value = null
  inboxGrid.value?.reload()
  sidebarKey.value++
}

// ── archive queue ──────────────────────────────────────────────────────────
// Transfers run here, not inside the preview panel, so you can pick the next
// video while the current one is still moving (its progress stays in the
// status bar instead of leaking into the newly-selected video's panel).
const queue = ref([])
const queueBusy = ref(false)

function enqueueArchive({ video, person }) {
  queue.value.push({ video, person })
  inboxGrid.value?.remove(video.path)   // it's spoken for — drop it from the grid
  processQueue()
}

async function processQueue() {
  if (queueBusy.value) return
  queueBusy.value = true
  try {
    while (queue.value.length) {
      const { video, person } = queue.value.shift()
      const waiting = queue.value.length
      try {
        const { job_id } = await api('/api/archive', {
          method: 'POST',
          body: JSON.stringify({ path: video.path, person }),
        })
        while (true) {
          await new Promise(r => setTimeout(r, 400))
          const j = await api(`/api/archive/job/${job_id}`)
          if (j.progress && typeof j.progress === 'object') {
            const p = j.progress
            const pct = p.total ? Math.round(p.done / p.total * 100) : 0
            const spd = p.speed >= 1 << 20 ? (p.speed / (1 << 20)).toFixed(1) + ' MB/s'
                       : p.speed ? (p.speed / 1024).toFixed(0) + ' KB/s' : ''
            setBusy(`移動中 ${p.name} ${pct}% ${spd}` +
                    (waiting ? `（還有 ${waiting} 個等待）` : ''))
          }
          if (j.status === 'done') {
            logArchived(video.name, person)
            setStatus(`已歸檔到「${person}」：${video.name}` +
                      (waiting ? `（還有 ${waiting} 個等待）` : ''), 'ok')
            sidebarKey.value++
            break
          }
          if (j.status === 'error') {
            setStatus(`歸檔失敗 ${video.name}：${j.error}`, 'warn')
            break
          }
        }
      } catch (e) {
        setStatus(`歸檔失敗 ${video.name}：${errText(e)}`, 'warn')
      }
    }
  } finally {
    queueBusy.value = false
    setBusy('')
  }
}
function onInboxDeleted(video) {
  inboxGrid.value?.remove(video.path)
  selectedInbox.value = null
}

const toastMsg = ref('')
let toastTimer = null
function toast(msg) {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMsg.value = '' }, 2500)
}
// '' = closed, otherwise the tab to open on ('video' | 'image')
const dupTab = ref('')
const showJav = ref(false)
const showConvert = ref(false)
// groups still holding at least one live file — the badge on the topbar button
const dupCount = computed(() => (store.dedup.groups || [])
  .filter(g => g.files.filter(f => !f.deleted).length > 1).length)
const imgDupCount = computed(() => (store.imgdedup.groups || [])
  .filter(g => g.files.filter(f => !f.deleted).length > 1).length)
// One button now, so its badge is both hunts and its spinner is either scan —
// the two are independent jobs and can run at the same time. Which is which
// is answered by the tabs inside, where there is room to say so.
const dupTotal = computed(() => dupCount.value + imgDupCount.value)
const dupRunning = computed(() =>
  store.dedup.running || store.imgdedup.running)
// Land on whichever hunt has something to look at. A running scan wins over a
// finished report, and video wins a tie because it is the one that reclaims
// real space.
const dupStart = computed(() => {
  if (store.dedup.running) return 'video'
  if (store.imgdedup.running) return 'image'
  return !dupCount.value && imgDupCount.value ? 'image' : 'video'
})
// The edit-mode tools, as data so the same list renders twice: as header
// buttons on a wide screen, and inside the 「工具」 menu below 1200px, where
// they no longer fit on one line (a tablet gets the desktop layout, and at
// 768-1150px all eight header buttons used to break onto two lines each).
// One list means a tool added later cannot show up in one place and not the
// other.
const toolItems = computed(() => !store.editMode ? [] : [
  can('person') && { key: 'face', icon: 'face',
    label: faceIndexing.value ? '索引中…' : '人臉索引',
    title: store.faceAvailable ? '為所有角色建立人臉索引' : store.faceReason,
    disabled: !store.faceAvailable || faceIndexing.value, run: runFaceIndex },
  can('lookup') && { key: 'lookup', icon: 'search', label: '角色查詢',
    title: '個資、片名、WIKI 匯入', run: () => { showJav.value = true } },
  can('convert', 'shrink') && { key: 'convert', icon: 'refresh', label: '轉檔',
    title: '把非 MP4 的影片轉成瀏覽器能播的 MP4',
    run: () => { showConvert.value = true } },
  can('dedup') && { key: 'dedup', icon: dupRunning.value ? 'spinner' : 'copy',
    spin: dupRunning.value, label: dupRunning.value ? '掃描中…' : '重複掃描',
    title: '找出重複的影片與圖片', badge: dupRunning.value ? 0 : dupTotal.value,
    run: () => { dupTab.value = dupStart.value } },
].filter(Boolean))
// What the closed menu has to say on its own: a scan or index in progress,
// and the duplicate count that used to sit on the 重複掃描 button.
const toolsBusy = computed(() => faceIndexing.value || dupRunning.value)
const toolMenu = ref(false)
function runTool(t) { toolMenu.value = false; t.run() }
const showSettings = ref(false)
const userMenu = ref(false)
const showPassword = ref(false)

function openPassword() { userMenu.value = false; showPassword.value = true }

// The server refuses everything else for this account until the password is
// changed, so the dialog is not optional — showing it is the only way the
// page can explain why nothing works.
const forcedPassword = computed(() => !!store.user?.must_change_pw)

const showSessions = ref(false)
function openSessions() { userMenu.value = false; showSessions.value = true }

const showTwoFactor = ref(false)
function openTwoFactor() { userMenu.value = false; showTwoFactor.value = true }
// Admin and editor accounts cannot use anything else until this is set up, so
// the dialog opens itself — exactly like the weak-password one above.
const forcedTwoFactor = computed(() =>
  !!store.user?.needs_2fa_setup && !forcedPassword.value)

async function onTwoFactorEnabled() {
  showTwoFactor.value = false
  try { store.user = await api('/api/auth/me') } catch { /* keep what we have */ }
}

// The server has already ended every session of this account; dropping the
// user here only makes the page follow, with a line saying why.
function onPasswordChanged() {
  showPassword.value = false
  store.loginNotice = '密碼已修改，請用新密碼重新登入'
  store.user = null
}
const showGroups = ref(false)
const sidebarKey = ref(0)   // bump to force sidebar reload after settings change

// Everything here is per-account: what the *current* user may see and do.
async function loadSessionConfig() {
  const cfg = await api('/api/config')
  store.defaultTab = cfg.default_browse_tab || 'videos'
  store.seekSeconds = cfg.seek_seconds || 10
  store.hasInbox = !!cfg.has_inbox
  try {
    const f = await api('/api/face/status')
    store.faceAvailable = !!f.available
    store.faceReason = f.reason || ''
  } catch { store.faceAvailable = false }
}

// A page reload forgets every job id, but the server kept working. Ask what is
// still running and pick the thread back up, otherwise the work finishes into
// the void and the user reruns it for nothing.
async function resumeJobs() {
  loadInterrupted()                      // deliberately not awaited
  if (!store.user?.can_edit) return
  let running = []
  try { running = (await api('/api/jobs')).jobs } catch { return }
  if (!running.length) return

  const scan = running.find(j => j.kind === 'dedup')
  if (scan) resumeScan(scan.id)          // deliberately not awaited
  const iscan = running.find(j => j.kind === 'imgdedup')
  if (iscan) resumeImgScan(iscan.id)

  // A convert scan is picked up only if it is this account's own (its rows
  // are paths): the per-user endpoint says which, the generic list cannot.
  let cscan = null
  if (can('convert') && running.some(j => j.kind.startsWith('convert_'))) {
    try { cscan = (await api('/api/convert/scan/active')).scan } catch { /* older backend */ }
    if (cscan) followScan(cscan.job_id, cscan.options, cscan.progress, !!cscan.stopping)
  }

  const others = running.filter(j => j !== scan && j !== iscan &&
                                      !(cscan && j.id === cscan.job_id))
  if (others.length) {
    setStatus('伺服器上還有進行中的作業：' +
              others.map(j => j.label).join('、'), 'warn')
  }
}

// Jobs a restart of the service cut short and that wait for this account:
// the ones that write files, and scans interrupted too often to rerun alone.
const interrupted = ref([])

async function loadInterrupted() {
  try { interrupted.value = (await api('/api/jobs/interrupted')).jobs || [] }
  catch { interrupted.value = [] }        /* older backend */
}

/** A job continued from that dialog: follow it where its kind is followed. */
async function onResumed(r) {
  if (r.kind === 'dedup') return resumeScan(r.job_id)
  if (r.kind === 'imgdedup') return resumeImgScan(r.job_id)
  if (r.kind?.startsWith('convert_') && r.kind !== 'convert_run') {
    try {
      const s = (await api('/api/convert/scan/active')).scan
      if (s) followScan(s.job_id, s.options, s.progress, !!s.stopping)
    } catch { /* the status bar line below still says it started */ }
    return
  }
  const j = await followJob(r.job_id, r.label)
  // files were converted, moved or written: lists and counts are stale
  if (j && j.status !== 'error') { sidebarKey.value++; store.filesVersion++ }
}

async function checkAuth() {
  try {
    store.user = await api('/api/auth/me')
    await loadSessionConfig()
    resumeJobs()
  } catch { store.user = null }
  finally { ready.value = true }
}

// Signing in as somebody else in the same tab must not inherit the previous
// account's config, cached counts or view state.
watch(() => store.user?.id, async (id, prev) => {
  if (!ready.value || id === prev) return
  resetUserState()
  interrupted.value = []                 // the previous account's, if any
  clearCharStats()
  sidebarKey.value++
  if (id) {
    await loadSessionConfig()
    loadInterrupted()                    // this account's own
  }
})

function onSettingsSaved() { sidebarKey.value++ }   // re-list characters

// Display settings for the group the open character belongs to. Defaults when
// it belongs to none — 未分類 is a section the sidebar derives, not a stored
// group, so there is nothing to configure for it.
const charView = computed(() =>
  viewForChar(store.selectedChar?.name, groupData.groups, groupData.view))

// Not `store.activeTab` directly. The tab it holds can be one this group hides
// — the navigation sites correct for that, but they run *before* /api/groups
// has answered on a cold load, and the global 預設分頁 setting knows nothing
// about groups either. Deciding it here means the page can never render with
// both the tab button and the content missing.
const shownTab = computed(() => pickTab(store.activeTab, charView.value))

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }) } catch { /* ignore */ }
  store.user = null      // the watcher above clears the rest
}

function toggleMode() { store.editMode = !store.editMode }

// The logo doubles as a home button. Same pair of assignments as the
// sidebar's 首頁 row — on a phone the sidebar is behind a drawer, so this is
// the only home affordance visible without opening it.
function goHome() { store.selectedChar = null; store.view = 'home' }

// Delete mode must never survive a context change — leaving edit mode, opening
// another character or switching view all put it back to off. Clip mode rides
// along: it is not dangerous, but a toggle still switched on three characters
// later is a surprise either way.
watch(() => [store.editMode, store.selectedChar?.path, store.view],
      () => { store.deleteMode = false; store.clipMode = false })

// ── narrow screens ──────────────────────────────────────────────────────────
// The sidebar is 260px of a 390px phone, so on mobile it becomes a drawer and
// the header's tool buttons (up to seven of them) fold into a menu. Both are
// mobile-only: the CSS keeps them permanently open above the breakpoint, so
// the desktop layout is untouched and these flags are simply ignored there.
const drawerOpen = ref(false)

// Picking a character is the whole point of the drawer, so it closes itself —
// otherwise every selection needs a second tap on the backdrop to see the
// result underneath.
watch(() => [store.selectedChar?.path, store.view], () => { drawerOpen.value = false })

onMounted(checkAuth)
</script>

<template>
  <div v-if="!ready" class="boot">載入中…</div>

  <LoginView v-else-if="!store.user" />

  <div v-else class="app" :class="{ chrome: drawerOpen }">
    <!-- Phone on its side only (hidden everywhere else by CSS): the header
         and status bar are gone to give the 390px height to the content, so
         this is the way back to the drawer and the user menu. It sits in a
         slim rail of its own rather than floating over the content, where it
         would cover whatever is in the top-left corner of each page. -->
    <button class="railbtn" aria-label="選單" @click="drawerOpen = true">
      <Icon name="rows" :size="20" />
    </button>
    <header class="topbar">
      <button class="iconbtn only-narrow" aria-label="選單"
              @click="drawerOpen = !drawerOpen">
        <Icon :name="drawerOpen ? 'close' : 'rows'" :size="18" />
      </button>
      <button class="brand ib" title="回到首頁" @click="goHome">
        <img class="mark" src="/brand-mark.png" alt="" />
        <span class="brandtext">Video Archive Manager</span>
      </button>
      <div class="spacer"></div>
      <div class="tools no-mobile">
      <button
        v-if="store.user.role === 'admin'"
        class="btn ghost ib" @click="showSettings = true"
      ><Icon name="settings" /> 設定</button>
      <!-- 角色查詢 is one button, three tabs: 個資 and 片名 were separate
           dialogs that shared a character picker and a scanning pattern, and
           WIKI 匯入 answers the same question from a different source.
           重複掃描 only reflects the scan's state; the scan lives in dedup.js. -->
      <button v-for="t in toolItems" :key="t.key"
              class="btn ghost ib wide-tool" :class="{ hasdup: t.badge > 0 }"
              :disabled="t.disabled" :title="t.title" @click="t.run()"
      ><Icon :name="t.icon" :class="{ spin: t.spin }" /> {{ t.label }}
        <span v-if="t.badge" class="dupbadge">{{ t.badge }}</span></button>
      </div>
      <!-- Below 1200px the tools above are hidden and live in here instead. -->
      <div v-if="toolItems.length" class="umenu toolmenu no-mobile">
        <button class="btn ghost ib" :class="{ on: toolMenu, hasdup: dupTotal > 0 && !dupRunning }"
                @click="toolMenu = !toolMenu" title="編輯工具">
          <Icon :name="toolsBusy ? 'spinner' : 'wrench'" :class="{ spin: toolsBusy }" /> 工具
          <span v-if="dupTotal && !dupRunning" class="dupbadge">{{ dupTotal }}</span>
          <Icon name="chevronDown" :size="12" />
        </button>
        <template v-if="toolMenu">
          <div class="umask" @click="toolMenu = false"></div>
          <div class="udrop">
            <button v-for="t in toolItems" :key="t.key" class="uitem ib"
                    :disabled="t.disabled" :title="t.title" @click="runTool(t)">
              <Icon :name="t.icon" :size="14" :class="{ spin: t.spin }" /> {{ t.label }}
              <span v-if="t.badge" class="dupbadge">{{ t.badge }}</span></button>
          </div>
        </template>
      </div>
      <button
        v-if="store.user.can_edit"
        class="btn mode-btn ib" :class="{ browse: !store.editMode }"
        :title="store.editMode ? '編輯模式' : '瀏覽模式'"
        @click="toggleMode"
      ><Icon :name="store.editMode ? 'wrench' : 'eye'" />
        <span class="mode-text">{{ store.editMode ? '編輯模式' : '瀏覽模式' }}</span></button>
      <!-- The user menu. On a phone only the icon shows (the name has no
           room), but the menu itself stays reachable there. -->
      <div class="umenu">
        <button class="btn ghost ib userbtn" :class="{ on: userMenu }"
                @click="userMenu = !userMenu" title="帳號">
          <Icon name="user" />
          <span class="who">{{ store.user.username }}
            <span class="role">{{ store.user.role_name || (store.user.can_edit ? '可編輯' : '唯讀') }}</span></span>
          <Icon name="chevronDown" :size="12" />
        </button>
        <template v-if="userMenu">
          <div class="umask" @click="userMenu = false"></div>
          <div class="udrop">
            <div class="uname">{{ store.user.username }}</div>
            <button class="uitem ib" @click="openPassword">
              <Icon name="settings" :size="14" /> 修改密碼</button>
            <button class="uitem ib" @click="openTwoFactor">
              <Icon name="check" :size="14" /> 兩步驟驗證
              <span v-if="store.user.twofa" class="uon">已啟用</span></button>
            <button class="uitem ib" @click="openSessions">
              <Icon name="gauge" :size="14" /> 登入中的裝置</button>
            <!-- Logout lives in here (the user's call) rather than as its own
                 header button: one fewer button on a crowded phone header,
                 and leaving takes a deliberate second tap. Set apart and red
                 so it does not read as one more setting. -->
            <div class="usep"></div>
            <button class="uitem ib out" @click="userMenu = false; logout()">
              <Icon name="logout" :size="14" /> 登出</button>
          </div>
        </template>
      </div>
    </header>

    <div class="body">
      <!-- the wrapper exists so App can position the sidebar as a drawer:
           scoped styles do not reach into the component, and :deep() on a
           wrapper is less fragile than exporting layout props -->
      <div class="sidewrap" :class="{ open: drawerOpen }">
        <CharacterSidebar :key="sidebarKey" @manage-groups="showGroups = true"
                          @merge="c => mergeChar = c"
                          @toast="m => setStatus(m, 'ok')" />
      </div>
      <div v-if="drawerOpen" class="scrim" @click="drawerOpen = false"></div>

      <main class="content">
        <!-- inbox view: grid on top, archive preview panel at the bottom -->
        <template v-if="store.view === 'inbox'">
          <div class="tabbar">
            <span class="crumb ib"><Icon name="inbox" /> 待分類</span>
            <button v-if="store.editMode && can('archive')"
                    class="logbtn ib no-mobile"
                    :class="{ on: selMode }" @click="toggleSelMode">
              <Icon :name="selMode ? 'close' : 'checked'" :size="13" />
              {{ selMode ? '結束選取' : '選取模式' }}
            </button>
            <button v-if="store.archived.length" class="logbtn ib"
                    @click="showLog = !showLog">
              本次已歸檔 {{ store.archived.length }} 部
              <Icon :name="showLog ? 'chevronDown' : 'chevronRight'" :size="13" />
            </button>
          </div>
          <div v-if="showLog && store.archived.length" class="logbox">
            <div v-for="(a, i) in store.archived" :key="i" class="logrow">
              <span class="lname" :title="a.name">{{ a.name }}</span>
              <span class="larrow">→</span>
              <span class="lperson">{{ a.person }}</span>
            </div>
          </div>
          <div class="inbox-wrap">
            <div class="inbox-grid">
              <VideoGrid ref="inboxGrid" scope="inbox"
                         @select="v => selectedInbox = v"
                         @archive-folder="f => folderToArchive = f"
                         @batch="items => batchSel = items" />
            </div>

            <!-- batch action bar -->
            <div v-if="selMode && batchSel.length" class="batchbar">
              <span class="bcount">已選 <b>{{ batchSel.length }}</b> 部 · {{ fmtSize(batchBytes) }}</span>
              <input v-model="batchPerson" list="charlist-batch" placeholder="歸檔到角色…" />
              <datalist id="charlist-batch">
                <option v-for="c in charNames" :key="c" :value="c" />
              </datalist>
              <button class="btn ib" @click="archiveSelected">
                <Icon name="check" :size="14" /> 歸檔</button>
              <button class="btn ghost" @click="inboxGrid?.clearSel(); batchSel = []">取消選取</button>
            </div>

            <PreviewPanel
              v-if="selectedInbox && !selMode"
              :video="selectedInbox"
              @close="selectedInbox = null"
              @skip="selectedInbox = null"
              @archive="enqueueArchive"
              @queued="selectedInbox = null"
              @deleted="onInboxDeleted"
              @marked="m => inboxGrid?.applyMark(m.path, m)"
            />
          </div>
        </template>

        <!-- character selected → tabs -->
        <template v-else-if="store.view === 'char' && store.selectedChar">
          <div class="tabbar">
            <span class="crumb ib">
              <Icon :name="store.selectedChar.merged ? 'warn' : 'folder'" />
              {{ store.selectedChar.name }}
              <span v-if="store.selectedChar.merged" class="mdrv">
                （合併視圖 {{ store.selectedChar.drives }}）</span>
            </span>
            <button v-if="charView.videos" class="tab ib"
                    :class="{ on: store.activeTab === 'videos' }"
                    @click="store.activeTab = 'videos'"><Icon name="film" /> 影片</button>
            <button v-if="charView.profile" class="tab ib"
                    :class="{ on: store.activeTab === 'profile' }"
                    @click="store.activeTab = 'profile'"><Icon name="profile" /> 個人資料</button>
          </div>
          <VideoGrid v-if="shownTab === 'videos'"
                     :root="store.selectedChar.merged ? null : store.selectedChar.path"
                     :merged="store.selectedChar.merged ? store.selectedChar.name : null"
                     :ratio="charView.ratio" :poster="charView.poster" />
          <ProfileTab v-else :folder="store.selectedChar.path" />
        </template>

        <HomeView v-else />
      </main>
    </div>

    <StatusBar />
    <SessionsDialog v-if="showSessions" @close="showSessions = false" />
    <InterruptedJobsDialog v-if="interrupted.length" :jobs="interrupted"
                           @close="interrupted = []" @resumed="onResumed" />
    <TwoFactorDialog v-if="showTwoFactor || forcedTwoFactor"
                     :forced="forcedTwoFactor" @close="showTwoFactor = false"
                     @logout="logout" @enabled="onTwoFactorEnabled" />
    <PasswordDialog v-if="showPassword || forcedPassword"
                    :forced="forcedPassword" :reason="store.user?.pw_problem || ''"
                    @close="showPassword = false" @logout="logout"
                    @changed="onPasswordChanged" />
    <div v-if="toastMsg" class="apptoast">{{ toastMsg }}</div>

    <DupDialog v-if="dupTab" :start="dupTab" @close="dupTab = ''" />
    <JavDialog v-if="showJav" @close="showJav = false" />
    <ConvertDialog v-if="showConvert" @close="showConvert = false"
                   @done="sidebarKey++; store.filesVersion++" />
    <SettingsDialog v-if="showSettings" @close="showSettings = false" @saved="onSettingsSaved" />
    <GroupManagerDialog v-if="showGroups" @close="showGroups = false" @saved="onSettingsSaved" />
    <MergeDialog v-if="mergeChar" :char="mergeChar"
                 @close="mergeChar = null" @done="onMergeDone" />
    <FolderArchiveDialog v-if="folderToArchive" :folder="folderToArchive"
                         @close="folderToArchive = null" @done="onFolderArchived" />
  </div>
</template>

<style scoped>
.boot { padding: 60px; text-align: center; color: var(--dim); }
.app { min-height: 100vh; min-height: 100dvh; }
.topbar {
  display: flex; align-items: center; gap: 12px; padding: 10px 16px;
  background: var(--panel); border-bottom: 1px solid #313148;
  position: sticky; top: 0; z-index: 20; height: 53px;
}
.brand { font-weight: bold; color: var(--accent);
  background: none; border: none; padding: 0; font: inherit; font-weight: bold;
  cursor: pointer; }
.brand:hover .mark { opacity: .8; }
/* height-constrained: the mark is wider than it is tall. It started at 20px,
   matching the cap height of the text beside it; 32px is the size asked for,
   which makes the mark lead rather than sit level with the wordmark.
   The header's content box is 33px (53px less 10px padding top and bottom),
   so this is as large as the mark can go without the header growing — and
   the header's height is baked into three `calc()` layout rules. */
.brand .mark { height: 32px; width: auto; display: block;
  transition: opacity .15s; }
.spacer { flex: 1; }

/* ── narrow screens ────────────────────────────────────────────────────────
   Everything mobile lives inside the media query below, so the desktop
   layout is byte-for-byte what it was. 760px rather than a phone width: the
   header runs out of room for its buttons long before a tablet does. */
.only-narrow { display: none; }
.iconbtn { background: none; border: none; color: var(--text); cursor: pointer;
  padding: 6px; border-radius: 6px; line-height: 0; }
.iconbtn:hover { background: var(--hover); }
/* `display: contents` on both wrappers means that above the breakpoint they
   are not layout boxes at all — the buttons and the sidebar stay exactly the
   flex children of .topbar and .body that they were before the wrappers
   existed. Without this the sidebar's own `flex-shrink: 0` would sit inside a
   *shrinkable* wrapper, and the 260px column could be squeezed on desktop. */
.tools { display: contents; }
.sidewrap { display: contents; }

@media (max-width: 760px), (pointer: coarse) and (max-height: 500px) {
  .only-narrow { display: inline-flex; }
  .topbar { gap: 6px; padding: 8px 10px; }
  /* the wordmark is the first thing to go — the logo still identifies it */
  .brandtext { display: none; }
  .who { display: none; }
  /* icon only; the colour already tells the two modes apart */
  .mode-text { display: none; }

  /* The tool group is hidden outright on a phone (see .no-mobile in
     style.css). An earlier version folded it into a dropdown, but every one
     of those buttons opens a dialog built for a wide window — offering them
     in a tidier menu would only make them easier to reach by mistake. */

  /* the sidebar: 260px of a 390px screen, so it slides in over the content */
  .sidewrap {
    display: block;
    position: fixed; top: 53px; bottom: 26px; left: 0; z-index: 30;
    width: min(300px, 84vw);
    transform: translateX(-100%);
    transition: transform .18s ease;
  }
  .sidewrap.open { transform: none; }
  .sidewrap :deep(.sidebar) { width: 100%; height: 100%; }
  .scrim { position: fixed; inset: 53px 0 26px 0; z-index: 29;
    background: rgba(0,0,0,.5); }

  /* touch targets: 11px text with 4px padding is a desktop affordance */
  .topbar :deep(.btn) { min-height: 40px; }
}

/* ── a phone held sideways ─────────────────────────────────────────────────
   390px of height: the 53px header and 26px status bar were a fifth of it.
   Both go; the header comes back (overlaid, not pushing the page) only while
   the drawer is open, because that is where the user menu (and logout in it) lives.
   Same query text as ../mobile.js LANDSCAPE_MQ — tests/mobile.test.mjs. */
.railbtn { display: none; }
@media (pointer: coarse) and (max-height: 500px) and (orientation: landscape) {
  .topbar { display: none; }
  .app.chrome .topbar { display: flex; position: fixed; top: 0; left: 0;
    right: 0; z-index: 32; }
  .app > :deep(.statusbar) { display: none; }
  .railbtn { display: flex; align-items: center; justify-content: center;
    position: fixed; left: 0; top: 0; bottom: 0; width: 32px; z-index: 28;
    background: var(--panel); border: none; border-right: 1px solid #313148;
    color: var(--text); padding: 0; cursor: pointer;
    /* the button's hit area is the whole rail, the icon sits near the top
       where the header's menu button used to be */
    align-items: flex-start; padding-top: 12px; }
  /* 32px, not 44: the whole rail is the hit area, so it can be narrow;
     iOS already adds its own blank strip for the notch beside it. */
  .body { padding-left: 32px; }
  /* `.body` in front for specificity: the base rules for these two sit
     further down the file, so a bare `.content` here lost to them and the
     page kept subtracting the hidden header and status bar — 79px of
     clipped, empty space along the bottom. */
  .body .content { height: 100vh; height: 100dvh; }
  .body .inbox-wrap { height: calc(100vh - 39px); height: calc(100dvh - 39px); }
  /* the drawer and its backdrop start at the header, which is showing now */
  .sidewrap { top: 53px; bottom: 0; }
  .scrim { inset: 53px 0 0 0; }
}
.mode-btn.browse { background: var(--card); color: var(--text); border: 1px solid #555; }
.hasdup { color: #f9e2af; }
.dupbadge { background: #f9e2af; color: #1a1a1a; border-radius: 8px;
  padding: 0 6px; font-size: 10px; font-weight: bold; }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.who { font-size: 13px; }
.umenu { position: relative; }
.userbtn.on { background: var(--hover); }
.umask { position: fixed; inset: 0; z-index: 70; }
.udrop { position: absolute; right: 0; top: calc(100% + 4px); z-index: 71;
  background: var(--panel); border: 1px solid #404058; border-radius: 8px;
  min-width: 160px; padding: 6px; box-shadow: 0 6px 20px rgba(0,0,0,.45); }
.uname { color: var(--dim); font-size: 11px; padding: 4px 8px 6px;
  border-bottom: 1px solid #313148; margin-bottom: 4px; }
.uitem { width: 100%; background: none; border: none; color: var(--text);
  font-size: 13px; padding: 8px; border-radius: 6px; cursor: pointer; text-align: left; }
.uitem:hover { background: var(--hover); }
.usep { border-top: 1px solid #313148; margin: 4px 0; }
.uitem.out { color: var(--danger); }
.uitem:disabled { color: var(--dim); cursor: default; background: none; }
.uitem .dupbadge { margin-left: 6px; }
.toolmenu .on { background: var(--hover); }

/* ── tablet width ──────────────────────────────────────────────────────────
   Tablets are past the phone breakpoint, so they get the desktop header —
   and in edit mode its eight buttons do not fit below about 1150px (measured
   at 768/834/1024: every one of them broke onto two lines). Under 1200px the
   four edit tools fold into the 「工具」 menu and the wordmark goes, keeping
   the logo. Phones hide both (`.no-mobile`); 1200px and up is unchanged. */
.toolmenu { display: none; }
@media (max-width: 1199px) {
  .wide-tool { display: none; }
  .toolmenu { display: block; }
  .brandtext { display: none; }
  .topbar :deep(.btn) { white-space: nowrap; }
}
.uon { color: #6cc28a; font-size: 10px; margin-left: 6px; }
.role { color: var(--dim); font-size: 11px; margin-left: 4px;
  border: 1px solid #444; border-radius: 8px; padding: 1px 6px; }

.body { display: flex; }
/* 53px topbar + 26px status bar */
.content { flex: 1; min-width: 0; overflow-y: auto;
  height: calc(100vh - 53px - 26px);
  /* dvh, not vh: on a phone the URL bar is inside 100vh, so the last
     26px of every page sat under the status bar and could not be
     scrolled to. The vh line above stays as the fallback. */
  height: calc(100dvh - 53px - 26px); }
.tabbar {
  display: flex; align-items: center; gap: 4px; padding: 0 14px;
  background: var(--panel); border-bottom: 1px solid #313148; position: sticky; top: 0; z-index: 10;
}
.crumb { font-size: 13px; font-weight: bold; margin-right: 12px; }
.mdrv { color: #f9e2af; font-size: 11px; font-weight: normal; }
.inbox-wrap { display: flex; flex-direction: column;
  height: calc(100vh - 53px - 26px - 39px);
  height: calc(100dvh - 53px - 26px - 39px); }
.logbtn { background: var(--card); border: 1px solid #555; border-radius: 4px;
  color: var(--text); font-size: 11px; padding: 3px 10px; margin-left: auto;
  margin-right: 4px; cursor: pointer; }
.logbtn:hover { border-color: var(--accent); }
.logbtn.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.batchbar { display: flex; align-items: center; gap: 10px;
  background: var(--panel); border-top: 2px solid var(--accent);
  padding: 10px 14px; }
.bcount { font-size: 12px; white-space: nowrap; }
.bcount b { color: var(--accent); }
.batchbar input { flex: 1; max-width: 280px; }
.logbox { background: var(--bg); border-bottom: 1px solid #313148;
  max-height: 150px; overflow-y: auto; padding: 6px 14px; }
.logrow { display: flex; gap: 8px; align-items: center; font-size: 11px;
  padding: 2px 0; color: var(--dim); }
.lname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.larrow { color: #555; }
.lperson { color: var(--success); }
.inbox-grid { flex: 1; overflow-y: auto; min-height: 0; }
.apptoast {
  position: fixed; bottom: 44px; left: 50%; transform: translateX(-50%);
  background: rgba(20,20,32,.95); border: 1px solid var(--accent);
  color: var(--text); padding: 10px 20px; border-radius: 8px;
  font-size: 13px; z-index: 80;
}
.tab {
  background: none; border: none; border-bottom: 2px solid transparent;
  color: var(--dim); padding: 10px 16px; font-size: 13px; cursor: pointer;
}
.tab:hover { color: var(--text); }
.tab.on { color: var(--text); border-bottom-color: var(--accent); }
</style>
