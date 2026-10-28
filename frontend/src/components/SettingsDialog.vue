<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { api, errText } from '../api'
import { store } from '../store'
import UserManager from './UserManager.vue'
import RoleManager from './RoleManager.vue'
import CacheHelp from './CacheHelp.vue'
import Icon from './Icon.vue'
import SideDialog from './SideDialog.vue'

const emit = defineEmits(['close'])

const inbox = ref('')
const archive = ref('')
const defaultArchive = ref('')
const defaultTab = ref('videos')
const seekSeconds = ref(10)
const sheetInterval = ref(60)
const sheetTileWidth = ref(480)
const sheetWorkers = ref(4)
const sheetHwaccel = ref('cpu')
const showWorkerHelp = ref(false)
const cpuCount = ref(0)
// Two budgets, not one. The single limit used to live on this tab, so it read
// as "the contact sheet limit" while quietly capping the video thumbnails too.
const thumbLimitGb = ref(2)
const sheetLimitGb = ref(2)
const photoLimitGb = ref(3)
const cacheUsed = ref(null)     // { thumbs: {used,limit_gb}, sheets: …, notes: … }
const sweeping = ref(false)
// One name at a time rather than a flag per field: two panels open at once
// pushes the second one off screen anyway, and this makes "which is open"
// impossible to get out of step.
const help = ref('')

async function toggleHelp(name) {
  help.value = help.value === name ? '' : name
  if (!help.value) return
  await nextTick()
  // `nearest`, so it only scrolls when the panel would otherwise be off
  // screen — opening a help box near the top should not move the page at all
  document.querySelector(`[data-help="${name}"]`)
    ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

// measured on a 1080p / 145-minute film, 12 grabs per sheet — the returns
// taper off sharply, which is not something you can guess from the number
const WORKER_MEASURED = [
  { n: 1, local: '2.78 s', nas: '3.40 s', note: '關閉平行（原本的行為）' },
  { n: 2, local: '1.94 s', nas: '—', note: '' },
  { n: 4, local: '1.57 s', nas: '2.07 s', note: '預設 —— 轉折點' },
  { n: 6, local: '1.67 s', nas: '2.03 s', note: '' },
  { n: 12, local: '1.54 s', nas: '—', note: '再多也沒用了' },
]

// measured on a 1920x1080 source, 4x3 tiles — the numbers people actually
// need to choose a width, rather than a bare pixel count
const TILE_CHOICES = [
  { w: 360, sheet: '1460x625', kb: 157, per11: '1.7 MB' },
  { w: 480, sheet: '1940x826', kb: 235, per11: '2.5 MB' },
  { w: 560, sheet: '2260x961', kb: 306, per11: '3.3 MB' },
  { w: 640, sheet: '2580x1096', kb: 361, per11: '3.9 MB' },
  { w: 800, sheet: '3220x1366', kb: 503, per11: '5.4 MB' },
]

function fmtBytes(n) {
  if (n == null) return '—'
  return n >= (1 << 30) ? (n / (1 << 30)).toFixed(2) + ' GB'
                        : (n / (1 << 20)).toFixed(0) + ' MB'
}

async function loadCacheUsage() {
  try { cacheUsed.value = await api('/api/cache/usage') } catch { /* ignore */ }
}
const usedOf = k => cacheUsed.value?.[k]?.used ?? null

// Says what happened even when the answer is "nothing needed doing" — the
// button silently rewrote the same number otherwise, which reads as broken.
const sweepMsg = ref('')

async function sweepCache() {
  sweeping.value = true
  sweepMsg.value = ''
  try {
    const r = await api('/api/cache/sweep', { method: 'POST' })
    await loadCacheUsage()
    // r[kind].limit_gb is what the server actually enforced. Using the field's
    // value here produced "407 MB does not exceed 0.1 GB": the sweep works
    // off the saved setting, and the field had not been saved yet.
    const field = { thumbs: thumbLimitGb, sheets: sheetLimitGb,
                    photos: photoLimitGb }
    const name = { thumbs: '影片縮圖', sheets: '縮圖表',
                   photos: '照片縮圖', shrink_samples: '壓縮試看片段',
                   gfriends: '圖庫頭像候選' }
    sweepMsg.value = ['thumbs', 'sheets', 'photos', 'shrink_samples', 'gfriends']
      .filter(k => r[k]).map(k => {
      const x = r[k]
      const unsaved = field[k] && Number(field[k].value) !== x.limit_gb
        ? `（畫面上的 ${field[k].value} GB 尚未儲存，請先按 OK）` : ''
      return x.removed
        ? `${name[k]}：刪了 ${x.removed} 個檔案，釋出 ${fmtBytes(x.before - x.after)}${unsaved}`
        : `${name[k]}：目前 ${fmtBytes(x.after)}，未超過已儲存的 ${x.limit_gb} GB 上限${unsaved}`
    }).join('\n')
  } catch (e) {
    sweepMsg.value = '失敗：' + errText(e)
  } finally { sweeping.value = false }
}
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const tab = ref('paths')      // paths | sheet | users | roles | backup | security
// The categories down the left side. Backup and the sign-in log load their
// data when opened, as their tabs did.
const NAV = [
  { key: 'paths', label: '資料夾與瀏覽', icon: 'folder' },
  { key: 'sheet', label: '縮圖表', icon: 'grid' },
  { key: 'users', label: '使用者', icon: 'user' },
  { key: 'roles', label: '權限組', icon: 'check' },
  { key: 'backup', label: '備份', icon: 'copy' },
  { key: 'security', label: '登入紀錄', icon: 'warn' },
]
// the badge on the sign-in log: failures in the last 7 days
const navItems = computed(() => NAV.map(n => n.key === 'security' && failed7d.value
  ? { ...n, badge: failed7d.value, warn: true } : n))
function go(key) {
  if (key === 'backup') openBackup()
  else if (key === 'security') openSecurity()
  else tab.value = key
}

// ── backups ────────────────────────────────────────────────────────────────
// Its own load/save rather than riding on the main OK button: turning backups
// on is the moment you want to find out the folder is unwritable, and the
// answer comes from the server.
const bk = ref(null)            // whatever /api/backup last said
const bkDir = ref('')
const bkKeep = ref(14)
const bkEnabled = ref(false)
const bkBusy = ref('')          // '', 'save', 'run'
const bkMsg = ref('')
const bkError = ref('')

async function loadBackup() {
  try {
    const r = await api('/api/backup')
    bk.value = r
    bkDir.value = r.dir || ''
    bkKeep.value = r.keep ?? 14
    bkEnabled.value = !!r.enabled
  } catch (e) { bkError.value = errText(e) }
}
function openBackup() { tab.value = 'backup'; loadBackup() }

// Judged from what is typed, not what was saved: the warning is only useful
// while someone is still choosing the folder.
const sameDrive = computed(() => {
  const d = (bkDir.value.trim().match(/^([a-zA-Z]):/) || [])[1]
  const data = (bk.value?.data_drive || '').replace(':', '')
  return !!d && !!data && d.toUpperCase() === data.toUpperCase()
})

async function saveBackup() {
  bkBusy.value = 'save'; bkError.value = ''; bkMsg.value = ''
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify({
      backup_enabled: bkEnabled.value,
      backup_dir: bkDir.value.trim(),
      backup_keep: Number(bkKeep.value) || 14,
    }) })
    await loadBackup()
    bkMsg.value = '已儲存'
  } catch (e) { bkError.value = errText(e) }
  finally { bkBusy.value = '' }
}

async function runBackup() {
  bkBusy.value = 'run'; bkError.value = ''; bkMsg.value = ''
  try {
    // Save what is on screen first. The server backs up to the *saved* folder,
    // so pressing this right after typing a path used to fail with
    // "請先指定備份資料夾" while the path sat right there in the box.
    await api('/api/settings', { method: 'POST', body: JSON.stringify({
      backup_enabled: bkEnabled.value,
      backup_dir: bkDir.value.trim(),
      backup_keep: Number(bkKeep.value) || 14,
    }) })
    const r = await api('/api/backup/run', { method: 'POST' })
    bkMsg.value = `備份完成：${r.files.length} 個檔案、${fmtBytes(r.bytes)}、`
                + `${r.seconds} 秒${r.pruned ? `，並刪掉 ${r.pruned} 份舊的` : ''}`
    await loadBackup()
  } catch (e) { bkError.value = errText(e) }
  finally { bkBusy.value = '' }
}

// ── login audit ────────────────────────────────────────────────────────────
const logs = ref([])
const failed7d = ref(0)
const logBusy = ref(false)

async function loadLogs() {
  logBusy.value = true
  try {
    const r = await api('/api/security/logins')
    logs.value = r.events || []
    failed7d.value = r.failed_7d || 0
  } catch { /* the badge simply stays as it was */ }
  finally { logBusy.value = false }
}

// The short list an admin should actually read, above the raw log.
const alerts = ref([])
const unseen = ref(0)

async function loadAlerts() {
  try {
    const r = await api('/api/security/alerts')
    alerts.value = r.alerts || []
    unseen.value = r.unseen || 0
  } catch { /* leave whatever was there */ }
}

async function markAlertsSeen() {
  try {
    await api('/api/security/alerts/seen', { method: 'POST' })
    unseen.value = 0
  } catch { /* the badge simply stays */ }
}

function openSecurity() { tab.value = 'security'; loadLogs(); loadAlerts() }

function fmtWhen(iso) {
  const d = new Date(iso)
  if (isNaN(d)) return iso
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// keep the default-root dropdown in sync with whatever is typed above
const archiveRootList = computed(() =>
  archive.value.split('\n').map(l => l.trim()).filter(Boolean))

function toLines(arr) { return (arr || []).join('\n') }
function fromLines(s) { return s.split('\n').map(l => l.trim()).filter(Boolean) }

async function load() {
  try {
    const s = await api('/api/settings')
    inbox.value = toLines(s.inbox_paths)
    archive.value = toLines(s.archive_roots)
    defaultArchive.value = s.default_archive_root || ''
    defaultTab.value = s.default_browse_tab || 'videos'
    seekSeconds.value = s.seek_seconds ?? 10
    sheetInterval.value = s.sheet_interval ?? 60
    sheetTileWidth.value = s.sheet_tile_width ?? 480
    sheetWorkers.value = s.sheet_workers ?? 4
    cpuCount.value = s.cpu_count ?? 0
    thumbLimitGb.value = s.thumb_limit_gb ?? 2
    sheetLimitGb.value = s.sheet_limit_gb ?? 2
    sheetHwaccel.value = s.sheet_hwaccel ?? 'cpu'
    photoLimitGb.value = s.photo_limit_gb ?? 3
    loadCacheUsage()
  } catch (e) {
    error.value = '載入失敗：' + errText(e)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        inbox_paths: fromLines(inbox.value),
        archive_roots: fromLines(archive.value),
        default_archive_root: defaultArchive.value.trim(),
        default_browse_tab: defaultTab.value,
        seek_seconds: Number(seekSeconds.value) || 10,
        sheet_interval: Number(sheetInterval.value) || 60,
        sheet_tile_width: Number(sheetTileWidth.value) || 480,
        sheet_workers: Number(sheetWorkers.value) || 4,
        thumb_limit_gb: Number(thumbLimitGb.value) || 2,
        sheet_limit_gb: Number(sheetLimitGb.value) || 2,
        sheet_hwaccel: sheetHwaccel.value,
        photo_limit_gb: Number(photoLimitGb.value) || 3,
      }),
    })
    store.defaultTab = defaultTab.value
    store.seekSeconds = Number(seekSeconds.value) || 10
    emit('saved')
    emit('close')
  } catch (e) {
    error.value = '儲存失敗：' + errText(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <!-- Categories on the left, the chosen one on the right (SideDialog): six
       tabs no longer fit one line of a 640px dialog, and the pages are long
       enough that a fixed list beside them reads better than a strip above. -->
  <SideDialog title="設定" :items="navItems" :model-value="tab"
              @update:model-value="go" @close="emit('close')">
        <div v-if="tab === 'users'" class="body"><UserManager /></div>
        <div v-else-if="tab === 'roles'" class="body"><RoleManager /></div>

        <div v-else-if="tab === 'backup'" class="body">
          <div class="fhint intro">
            複製 <b>{{ (bk?.sources || ['vam.db']).join('、') }}</b> 到你指定的資料夾。
            帳號、看過／最愛、分類、設定、相似門檻都在 <b>vam.db</b> 裡，
            <b>這些東西壞了或誤刪就再也回不來</b> —— 快取則是隨時可以重新產生的，所以不備份。
            每天一次，服務啟動時若距離上次超過一天也會補跑一次。
          </div>
          <div class="card">
            <label class="item line">
              <span class="lab">啟用自動備份</span>
              <input type="checkbox" class="cb" v-model="bkEnabled" />
            </label>
            <div class="item">
              <label class="lab">備份資料夾</label>
              <input v-model="bkDir" placeholder="D:\VAM備份" spellcheck="false" />
              <div class="fhint">
                <b>請指定另一顆實體硬碟或 NAS。</b>
                跟資料放在同一顆硬碟的話，那顆硬碟壞掉時備份會一起消失。
              </div>
              <div v-if="sameDrive" class="fhint warnbox">
                <Icon name="warn" :size="13" />
                這個位置跟 VAM 的資料在同一個磁碟機代號 —— 誤刪救得回來，硬碟壞掉救不回來。
              </div>
            </div>
            <div class="item">
              <div class="line">
                <label class="lab">保留份數</label>
                <input type="number" v-model.number="bkKeep" min="2" max="90" style="width:100px" />
              </div>
              <div class="fhint">
                超過的會自動刪掉最舊的。每份約 {{ fmtBytes((bk?.runs?.[0]?.bytes) || 27000000) }}。
              </div>
            </div>
            <div class="item">
              <div class="frow">
                <button class="btn" :disabled="bkBusy" @click="saveBackup">
                  {{ bkBusy === 'save' ? '儲存中…' : '儲存' }}</button>
                <button class="btn ghost" :disabled="bkBusy || !bkDir" @click="runBackup">
                  {{ bkBusy === 'run' ? '備份中…' : '立即備份一次' }}</button>
              </div>
              <div v-if="bkMsg" class="okmsg">{{ bkMsg }}</div>
              <div v-if="bkError" class="err">{{ bkError }}</div>
            </div>
          </div>

          <div class="loghead">
            狀態
            <span class="spacer"></span>
            <button class="lnk" @click="loadBackup">重新整理</button>
          </div>
          <div class="fhint">
            <template v-if="bk?.last_error">
              <span class="bad">上次失敗：{{ bk.last_error }}</span>
            </template>
            <template v-else-if="bk?.last_at">
              上次備份：{{ fmtWhen(bk.last_at) }}
            </template>
            <template v-else>還沒有備份過。</template>
          </div>
          <div class="logbox">
            <div v-if="!(bk?.runs || []).length" class="fhint">這個資料夾裡還沒有備份。</div>
            <div v-for="r in (bk?.runs || [])" :key="r.name" class="logrow">
              <span class="lu">{{ r.name }}</span>
              <span class="li">{{ fmtBytes(r.bytes) }}</span>
            </div>
          </div>
        </div>

        <div v-else-if="tab === 'security'" class="body">
          <div class="card note">
            <div class="fhint">
              每一次登入嘗試都會記錄，成功與失敗都有。<b>連續 5 次失敗後會開始鎖定</b>
              （60 秒起，持續失敗逐步拉長到 1 小時），計算方式是「帳號 + 來源 IP」的組合
              —— 只看帳號的話，任何人都能用亂猜把你的帳號鎖住。
            </div>
            <div class="fhint">
              從網際網路連線請一律走 <b>https:// 的網址</b>（有正式憑證，全程加密）。
              內網的 <b>http://…:8301</b> 仍是明文，只適合在自己家裡用。
            </div>
          </div>
          <div class="loghead">
            值得看一眼的事件
            <span v-if="unseen" class="bad">· {{ unseen }} 筆未讀</span>
            <span class="spacer"></span>
            <button v-if="unseen" class="lnk" @click="markAlertsSeen">全部標為已讀</button>
          </div>
          <div class="logbox alerts">
            <div v-if="!alerts.length" class="fhint">目前沒有異常。</div>
            <div v-for="a in alerts" :key="a.id" class="logrow alert">
              <span class="lt">{{ fmtWhen(a.at) }}</span>
              <span class="lk">{{ a.label }}</span>
              <span class="lu">{{ a.username || '—' }}</span>
              <span class="li">{{ a.ip || '' }}</span>
              <span class="lr">{{ a.detail }}</span>
            </div>
          </div>

          <div class="loghead">
            最近 {{ logs.length }} 筆
            <span v-if="failed7d" class="bad">· 7 天內失敗 {{ failed7d }} 次</span>
            <span class="spacer"></span>
            <button class="lnk" :disabled="logBusy" @click="loadLogs">重新整理</button>
          </div>
          <div class="logbox">
            <div v-if="!logs.length" class="fhint">還沒有任何紀錄。</div>
            <div v-for="(e, i) in logs" :key="i" class="logrow" :class="{ bad: !e.ok }">
              <span class="lt">{{ fmtWhen(e.at) }}</span>
              <span class="lu" :title="e.username">{{ e.username }}</span>
              <span class="li">{{ e.ip }}</span>
              <span class="lr">{{ e.ok ? '成功' : (e.reason || '失敗') }}</span>
            </div>
          </div>
        </div>

        <div v-else-if="tab === 'sheet'" class="body">
          <!-- One shape for every row: label + ? and the control on one line,
               then at most one live line under it. Anything that explains
               rather than reports lives behind the ?, opened right under its
               own row, because it is read once and then never again, while
               the numbers under the controls change as you type. -->
          <div class="sec">縮圖表</div>
          <div class="card">
            <div class="item">
              <div class="line">
                <label class="lab">取樣間隔（秒）
                  <button class="qmark" @click="toggleHelp('interval')">?</button>
                </label>
                <input type="number" v-model.number="sheetInterval" min="5" max="600" style="width:100px" />
              </div>
              <div class="fhint">
                一張涵蓋 {{ Math.round(sheetInterval * 12 / 60) }} 分鐘 ·
                兩小時的影片約 {{ Math.max(1, Math.ceil(7200 / (sheetInterval * 12))) }} 張
              </div>
              <div v-if="help === 'interval'" data-help="interval" class="helptbl">
                <div class="hn">
                  每 {{ sheetInterval }} 秒取一格、12 格拼成一張。間隔越短，畫面漏掉的越少，
                  但張數與產生時間成正比增加 —— 這是<b>唯一能減少總工作量的設定</b>，
                  其他選項都只是讓同樣的工作跑得快一點。
                </div>
              </div>
            </div>

            <div class="item">
              <div class="line">
                <label class="lab">
                  每格寬度
                  <button class="qmark" @click="toggleHelp('tile')">?</button>
                </label>
                <select v-model.number="sheetTileWidth" style="width:220px">
                  <option v-for="c in TILE_CHOICES" :key="c.w" :value="c.w">
                    {{ c.w }} px（整張 {{ c.sheet }}）
                  </option>
                </select>
              </div>
              <div v-if="help === 'tile'" data-help="tile" class="helptbl">
                <div class="hh">實測（1920×1080 來源、4×3 格、123 分鐘的片共 11 張）</div>
                <table>
                  <thead>
                    <tr><th>每格寬</th><th>整張尺寸</th><th>單張</th><th>11 張合計</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="c in TILE_CHOICES" :key="c.w"
                        :class="{ cur: c.w === sheetTileWidth }">
                      <td>{{ c.w }} px</td><td>{{ c.sheet }}</td>
                      <td>{{ c.kb }} KB</td><td>{{ c.per11 }}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="hn">
                  產生時間與寬度無關（都是約 2.8 秒）—— 成本在跳轉與解碼，縮放幾乎免費。
                  改這個值不會覆蓋既有的縮圖表，而是另外產生一份新尺寸的。
                </div>
              </div>
            </div>

            <div class="item">
              <div class="line">
                <label class="lab">
                  同時抓幾格
                  <button class="qmark" @click="toggleHelp('worker')">?</button>
                </label>
                <input type="number" v-model.number="sheetWorkers" min="1"
                       :max="cpuCount || 12" step="1" style="width:100px" />
              </div>
              <div class="fhint">
                <template v-if="cpuCount">這台機器有 {{ cpuCount }} 核 · </template>
                超過 4 之後幾乎沒有差別 · 填 1 ＝關閉平行
              </div>
              <div v-if="help === 'worker'" data-help="worker" class="helptbl">
                <div class="hh">實測（1080p、145 分鐘的片，產生一張縮圖表）</div>
                <table>
                  <thead>
                    <tr><th>同時幾格</th><th>本機硬碟</th><th>NAS</th><th></th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="w in WORKER_MEASURED" :key="w.n"
                        :class="{ cur: w.n === sheetWorkers }">
                      <td>{{ w.n }}</td><td>{{ w.local }}</td><td>{{ w.nas }}</td>
                      <td>{{ w.note }}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="hn">
                  一張表要抓 12 格，每格都是獨立的 ffmpeg，所以同時跑會快。但探測與拼貼
                  <b>不能平行</b>，它們替整體時間設下地板 —— 這就是 4 之後拉平的原因。
                  <br>這個設定<b>不影響產出的圖</b>，只改產生的順序；既有的縮圖表不會失效，
                  改完不用重啟，下一張就套用。
                  <br>選 GPU 時這個數字會另外<b>夾在 4</b>：顯示記憶體不像 CPU，用完是整台
                  機器倒下，一路 4K 解碼約佔 400 MiB。
                </div>
              </div>
            </div>

            <div class="item">
              <label class="lab">運算方式
                <button class="qmark" @click="toggleHelp('hw')">?</button>
              </label>
              <div class="hwrow">
                <label class="hw" :class="{ on: sheetHwaccel === 'cpu' }">
                  <input type="radio" value="cpu" v-model="sheetHwaccel" />
                  <b>CPU</b><span>快一點，但會吃掉大部分核心</span>
                </label>
                <label class="hw" :class="{ on: sheetHwaccel === 'gpu' }">
                  <input type="radio" value="gpu" v-model="sheetHwaccel" />
                  <b>GPU</b><span>慢約三成，但幾乎不佔 CPU</span>
                </label>
              </div>
              <div v-if="help === 'hw'" data-help="hw" class="helptbl">
                <div class="hh">實測（同一支 4K 影片、12 格一張）</div>
                <table>
                  <thead>
                    <tr><th></th><th>耗時</th><th>燒掉的 CPU</th><th>約佔幾核</th></tr>
                  </thead>
                  <tbody>
                    <tr :class="{ cur: sheetHwaccel === 'cpu' }">
                      <td>CPU</td><td>3.60 秒</td><td>37.0 秒</td><td>10.3</td></tr>
                    <tr :class="{ cur: sheetHwaccel === 'gpu' }">
                      <td>GPU</td><td>4.60 秒</td><td>3.4 秒</td><td>0.7</td></tr>
                  </tbody>
                </table>
                <div class="hn">
                  <b>GPU 比較慢，選它是為了把核心讓出來</b> —— 多人同時使用、或一邊在轉檔／
                  壓縮時，這個差別遠比那一秒重要。
                  <br>省下的 CPU 跟解析度有關：4K 省 10.6 倍，1080p 以下幾乎沒有差別
                  （固定成本是開行程、解析容器、編 JPEG，那些 GPU 幫不上忙）。
                  <br>影片解碼走顯示卡上的專用區塊（NVDEC），跟遊戲用的運算核心是分開的。
                  NVDEC 不支援的編碼會<b>自動退回 CPU</b>，不會因此產不出縮圖表。
                  <br>這個選擇<b>也套用到「影片處理」的完整解碼檢查</b>：同樣是「誰來負擔解碼」，
                  那邊 CPU 每支吃掉 5～9 核、GPU 不到半核，找出的損壞一樣。
                </div>
              </div>
            </div>
          </div>

          <div class="sec">快取上限</div>
          <div class="card">
            <div class="item">
              <div class="line">
                <label class="lab">縮圖表快取上限（GB）
                  <button class="qmark" @click="toggleHelp('sheets')">?</button>
                </label>
                <input type="number" v-model.number="sheetLimitGb" min="0.1" max="200"
                       step="0.5" style="width:100px" />
              </div>
              <div class="fhint usage">
                目前用量 <b>{{ fmtBytes(usedOf('sheets')) }}</b> / {{ sheetLimitGb }} GB
              </div>
              <CacheHelp v-if="help === 'sheets'" data-help="sheets" />
            </div>

            <div class="item">
              <div class="line">
                <label class="lab">影片縮圖快取上限（GB）
                  <button class="qmark" @click="toggleHelp('thumbs')">?</button>
                </label>
                <input type="number" v-model.number="thumbLimitGb" min="0.1" max="200"
                       step="0.5" style="width:100px" />
              </div>
              <div class="fhint usage">
                目前用量 <b>{{ fmtBytes(usedOf('thumbs')) }}</b> / {{ thumbLimitGb }} GB
              </div>
              <CacheHelp v-if="help === 'thumbs'" data-help="thumbs" />
            </div>

            <div class="item">
              <div class="line">
                <label class="lab">照片縮圖快取上限（GB）
                  <button class="qmark" @click="toggleHelp('photos')">?</button>
                </label>
                <input type="number" v-model.number="photoLimitGb" min="0.1" max="200"
                       step="0.5" style="width:100px" />
              </div>
              <div class="fhint usage">
                目前用量 <b>{{ fmtBytes(usedOf('photos')) }}</b> / {{ photoLimitGb }} GB
                ·　壓縮試看片段 <b>{{ fmtBytes(usedOf('shrink_samples')) }}</b> / 0.5 GB（固定）
                ·　圖庫頭像候選 <b>{{ fmtBytes(usedOf('gfriends')) }}</b> / 0.3 GB（固定）
              </div>
              <CacheHelp v-if="help === 'photos'" data-help="photos" />
            </div>

            <div class="item">
              <!-- not "清理": it applies the limits, and when nothing is over
                   them the correct outcome is to delete nothing -->
              <button class="lnk flat" :disabled="sweeping" @click="sweepCache">
                {{ sweeping ? '處理中…' : '立即套用上限' }}
              </button>
              <div v-if="sweepMsg" class="fhint sweepmsg">{{ sweepMsg }}</div>
            </div>
          </div>

          <div v-if="error" class="err">{{ error }}</div>
        </div>

        <div v-else-if="loading" class="msg">載入中…</div>
        <div v-else class="body">
          <div class="sec">資料夾</div>
          <div class="card">
            <div class="item">
              <label class="lab">待分類資料夾（新影片放這裡，每行一個）</label>
              <textarea v-model="inbox" rows="3" placeholder="D:\PORN"></textarea>
            </div>
            <div class="item">
              <label class="lab">歸檔根目錄（角色資料夾會建在這些位置下，每行一個）</label>
              <textarea v-model="archive" rows="4" placeholder="G:\Social Media"></textarea>
            </div>
            <div class="item">
              <label class="lab">新角色資料夾預設建在哪個根目錄</label>
              <select v-model="defaultArchive">
                <option value="">（自動：使用第一個歸檔根目錄）</option>
                <option v-for="r in archiveRootList" :key="r" :value="r">{{ r }}</option>
              </select>
              <div v-if="defaultArchive && !archiveRootList.includes(defaultArchive)" class="warn">
                目前的預設根目錄不在上方清單中，歸檔時會自動改用第一個歸檔根目錄
              </div>
            </div>
          </div>

          <div class="sec">瀏覽與播放</div>
          <div class="card">
            <div class="item line">
              <label class="lab">點選角色後預設顯示的分頁</label>
              <select v-model="defaultTab" style="width:160px">
                <option value="videos">影片</option>
                <option value="profile">個人資料</option>
              </select>
            </div>
            <div class="item line">
              <label class="lab">方向鍵快進/快退秒數</label>
              <input type="number" v-model.number="seekSeconds" min="1" max="300" style="width:100px" />
            </div>
          </div>

          <div v-if="error" class="err">{{ error }}</div>
        </div>

        <div class="foot">
          <template v-if="tab === 'paths' || tab === 'sheet'">
            <button class="btn" :disabled="saving || loading" @click="save">
              {{ saving ? '儲存中…' : 'OK' }}
            </button>
            <button class="btn ghost" @click="emit('close')">Cancel</button>
          </template>
          <!-- the user tab saves each change on its own -->
          <button v-else class="btn" @click="emit('close')">關閉</button>
        </div>
  </SideDialog>
</template>

<style scoped>
.okmsg { color: #6cc28a; font-size: 12px; margin-top: 6px; }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px; margin-top: 10px; }
/* The dialog makes every input full width (below), which turned this checkbox
   into a centred box with its label pushed out of sight. */
.chk input { width: auto; flex: 0 0 auto; margin: 0; }
.frow { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
.fhint .bad { color: var(--danger); }
/* the one scroller; padding at the bottom so the last card never looks cut */
.body { flex: 1; min-height: 0; padding: 6px 24px 24px; overflow-y: auto;
  display: flex; flex-direction: column; gap: 6px; }
.sec { font-size: 12px; color: var(--dim); margin: 12px 2px 2px; }
.sec:first-child { margin-top: 0; }
/* a card of rows: name on the left, the control on the right, anything that
   reports or explains (hints, the ? panels) underneath its own row */
.card { background: var(--card); border-radius: 12px; padding: 0 16px;
  border: 1px solid #ffffff0d; }
.card.note { padding: 6px 16px 10px; }
.item { display: flex; flex-direction: column; gap: 6px; padding: 12px 0;
  border-bottom: 1px solid #ffffff12; }
.item:last-child { border-bottom: none; }
.line { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.item.line { flex-direction: row; }
.line > :not(.lab) { flex: 0 0 auto; }
.lab { font-size: 13px; font-weight: 500; margin: 0; }
.card .fhint { margin-top: 0; }
.cb { width: 18px !important; height: 18px; margin: 0; accent-color: var(--accent);
  cursor: pointer; }
label.item.line { cursor: pointer; }
.lnk.flat { margin-left: 0; padding: 0; align-self: flex-start; font-size: 12px; }
.fhint.intro { margin: 0 2px 6px; }
/* scoped styles do not travel — .fhint is defined in ProfileTab.vue too, and
   without a copy here the hint rendered at full body size */
.fhint { color: var(--dim); font-size: 11px; margin-top: 4px; line-height: 1.6; }
.qmark { background: #313148; border: none; color: var(--dim); border-radius: 50%;
  width: 16px; height: 16px; font-size: 11px; cursor: pointer; margin-left: 5px; }
.qmark:hover { background: var(--accent); color: #fff; }
.helptbl { margin-top: 6px; border: 1px solid #313148; border-radius: 6px;
  padding: 8px 10px; font-size: 11px; }
.helptbl .hh { color: var(--dim); margin-bottom: 6px; }
.helptbl table { border-collapse: collapse; width: 100%; }
.helptbl th { color: var(--dim); font-weight: normal; text-align: left;
  padding: 2px 10px 4px 0; }
.helptbl td { padding: 2px 10px 2px 0; }
.helptbl tr.cur td { color: var(--accent); font-weight: bold; }
.helptbl .hn { color: var(--dim); margin-top: 7px; line-height: 1.6; }
.usage { margin-top: 5px; }
.usage b { color: var(--accent); }
.sweepmsg { margin-top: 4px; color: #7ddba0; line-height: 1.6;
  white-space: pre-line; }   /* two lines now, one per cache kind */
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; margin-left: 8px; }
textarea, input, select { width: 100%; font-family: inherit; }
textarea { resize: vertical; }
select { background: var(--card); color: var(--text); border: 1px solid #555;
  border-radius: 5px; padding: 8px; }
.foot { display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 24px; border-top: 1px solid #313148; }
.msg { padding: 30px; text-align: center; color: var(--dim); }
.hwrow { display: flex; gap: 8px; }
.hw { flex: 1; display: flex; flex-direction: column; gap: 2px; cursor: pointer;
  border: 1px solid var(--line, #313148); border-radius: 6px; padding: 8px 10px; }
.hw.on { border-color: var(--accent); background: #7c7cf014; }
.hw b { font-size: 13px; }
.hw span { color: var(--dim); font-size: 11px; }
.mini { border-collapse: collapse; margin: 6px 0; }
.mini th, .mini td { text-align: left; padding: 1px 12px 1px 0; font-weight: normal;
  color: var(--dim); }
.mini td b { color: var(--text); }
.warnbadge { background: var(--danger); color: #1e1e2e; border-radius: 8px;
  font-size: 10px; padding: 0 5px; margin-left: 4px; font-weight: bold; }
.warnbox { border: 1px solid #f38ba844; background: #f38ba814; color: #f38ba8;
  border-radius: 6px; padding: 8px 10px; display: flex; gap: 8px;
  align-items: flex-start; }
.warnbox b { color: #f38ba8; }
.loghead { display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: var(--dim); margin: 10px 0 4px; }
.loghead .bad { color: var(--danger); }
.logbox { max-height: 46vh; max-height: 46dvh; overflow-y: auto;
  border: 1px solid #313148; border-radius: 6px; }
.logrow { display: flex; gap: 10px; font-size: 11px; padding: 4px 8px;
  border-bottom: 1px solid #2a2a3c; font-variant-numeric: tabular-nums; }
.logrow.bad { color: var(--danger); }
.logbox.alerts { max-height: 26vh; }
.logrow.alert .lk { width: 150px; flex: 0 0 auto; color: #e9a24a; }
.logrow.alert .lr { color: var(--dim); }
.lt { width: 88px; flex: 0 0 auto; color: var(--dim); }
.lu { width: 110px; flex: 0 0 auto; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.li { width: 120px; flex: 0 0 auto; color: var(--dim); }
.lr { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.err { color: var(--danger); font-size: 13px; margin-top: 8px; }
.warn { color: #f9e2af; font-size: 11px; margin-top: 4px; }
@media (max-width: 720px) {
  .body { padding: 6px 16px 20px; }
}
</style>
