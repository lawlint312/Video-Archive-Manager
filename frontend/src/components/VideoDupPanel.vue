<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { api, streamUrl, errText } from '../api'
import { store, can } from '../store'
import { runScan, resumeScan, stopScan, loadSavedReport, rescore, manualCount,
         fmtSize, fmtDur, fmtDate } from '../dedup'
import { rememberPicking, wasPicking } from '../dedup'
import { openHistoryReport } from '../dedup'
import { releaseFile } from '../player'
import ScanProgress from './ScanProgress.vue'
import ScopePicker from './ScopePicker.vue'
import VideoPlayer from './VideoPlayer.vue'
import Icon from './Icon.vue'
import DupHistory from './DupHistory.vue'


const dedup = store.dedup
const threshold = ref(1)
const tier2 = ref(false)
const error = ref('')

// ── scope picker (shared component; see ScopePicker.vue) ────────────────────
// Open on the picker when there is no report, or when the user last left
// this report with "重新掃描" (see rememberPicking in dedup.js).
const scopeOpen = ref(!dedup.running && (dedup.groups === null || wasPicking('video')))
const showHistory = ref(false)

function reopenScope() {
  scopeOpen.value = true
  rememberPicking('video')
}

async function openHist(id) {
  error.value = ''
  try {
    await openHistoryReport(id)
    showHistory.value = false
    scopeOpen.value = false
    filter.value = 'all'
  } catch (e) { error.value = '開啟紀錄失敗：' + errText(e) }
}
const picker = ref(null)
const selected = ref([])
const LS_OPTS = 'vam.dedup.opts'

function persist() {
  localStorage.setItem(LS_OPTS, JSON.stringify({ tier2: tier2.value, threshold: threshold.value }))
}

const inboxOn = computed(() => picker.value?.inboxOn ?? 0)
const charsOn = computed(() => picker.value?.charsOn ?? 0)

onMounted(async () => {
  try {
    const o = JSON.parse(localStorage.getItem(LS_OPTS) || '{}')
    if (typeof o.tier2 === 'boolean') tier2.value = o.tier2
    if (typeof o.threshold === 'number') threshold.value = o.threshold
  } catch { /* ignore malformed prefs */ }
  await loadSavedReport()
  if (dedup.groups?.length && !wasPicking('video')) scopeOpen.value = false
})

function fmtWhen(t) {
  if (!t) return ''
  const d = new Date(t * 1000)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function scan() {
  if (!selected.value.length) { error.value = '請至少選擇一個資料夾'; return }
  error.value = ''
  persist()
  scopeOpen.value = false
  filter.value = 'all'
  viewed.value = null
  await runScan({ roots: selected.value, tier2: tier2.value,
                  threshold: threshold.value })
}

// ── report ──────────────────────────────────────────────────────────────────
const filter = ref('all')          // all | manual | tier2
const playing = ref(null)          // file entry currently in the player
// The "目前檢視中" marker outlives the player: the whole point is to tell you
// which group you were just looking at once the video is closed again.
const viewed = ref(null)
const resultsEl = ref(null)

function play(f) { viewed.value = f; playing.value = f }

async function closePlayer() {
  playing.value = null
  await nextTick()
  resultsEl.value?.querySelector('.card.active')
    ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

// Reviewing duplicates means watching a file and then deleting it, so the
// player is usually still holding the very file being deleted. Let go first —
// only when the doomed set really includes what is on screen, otherwise a
// batch delete would close a video you are still comparing against.
async function releaseIfPlaying(paths) {
  if (!playing.value || !paths.includes(playing.value.path)) return
  await releaseFile(() => { playing.value = null })
}

const groups = computed(() => dedup.groups || [])
const tier1 = computed(() => groups.value.filter(g => g.tier === 1))
const tier2Groups = computed(() => groups.value.filter(g => g.tier === 2))
const certainN = computed(() => tier1.value.filter(g => g.certain).length)
const uncertainN = computed(() => tier1.value.filter(g => !g.certain).length)
const manualN = computed(() => manualCount(groups.value))

// wasted space = every copy but the smallest one in each group
const totalWaste = computed(() => groups.value.reduce((n, g) => {
  const live = g.files.filter(f => !f.deleted)
  if (live.length < 2) return n
  return n + Math.min(...live.map(f => f.size)) * (live.length - 1)
}, 0))

// what "執行 Tier 1 建議" would delete
const autoTargets = computed(() => {
  const out = []
  for (const g of tier1.value) {
    if (!g.certain || !g.keeper || g.touched) continue
    for (const f of g.files) {
      if (f.path !== g.keeper && !f.deleted) out.push({ g, f })
    }
  }
  return out
})
const autoBytes = computed(() => autoTargets.value.reduce((n, t) => n + t.f.size, 0))

const shown = computed(() => {
  const list = groups.value.filter(g =>
    filter.value === 'all' ? true
    : filter.value === 'tier2' ? g.tier === 2
    : (g.tier === 2 || !g.certain))
  // Tier 1 first, then Tier 2 closest-match first — with hundreds of groups the
  // ones worth acting on have to be at the top
  return [...list].sort((a, b) =>
    (a.tier - b.tier) || ((a.distance ?? 99) - (b.distance ?? 99)))
})

function bestDur(g) { return Math.max(...g.files.map(f => f.duration || 0)) }
function bestPx(g) { return Math.max(...g.files.map(f => (f.width || 0) * (f.height || 0))) }
function rowClass(g, f) {
  if (f.deleted) return 'gone'
  if (!g.certain) return 'unsure'
  return f.path === g.keeper ? 'keep' : 'del'
}

// A failure used to be an alert you dismissed and then had to remember. Pin
// the reason to the row instead, and let the whole set be retried at once —
// the usual cause is transient (something else still had the file open).
const byPath = computed(() => {
  const m = new Map()
  for (const g of groups.value) for (const f of g.files) m.set(f.path, { g, f })
  return m
})

function reportErrors(errors) {
  for (const e of errors || []) {
    const hit = byPath.value.get(e.path)
    if (hit) hit.f.error = e.error
  }
}

const failed = computed(() => {
  const out = []
  for (const g of groups.value) {
    for (const f of g.files) if (f.error && !f.deleted) out.push({ g, f })
  }
  return out
})

async function retryFailed() {
  const targets = failed.value
  if (!targets.length) return
  executing.value = true
  try {
    for (const t of targets) t.f.error = null
    const paths = targets.map(t => t.f.path)
    await releaseIfPlaying(paths)
    const r = await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ paths }),
    })
    const gone = new Set(r.deleted)
    for (const t of targets) {
      if (gone.has(t.f.path)) { t.f.deleted = true; t.g.touched = true }
    }
    for (const g of groups.value) rescore(g)
    reportErrors(r.errors)
  } catch (e) {
    alert('重試失敗：' + errText(e))
  } finally { executing.value = false }
}

async function delOne(g, f) {
  if (!confirm(`確定要永久刪除？\n\n${f.name}`)) return
  f.error = null
  try {
    await releaseIfPlaying([f.path])
    await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ path: f.path }),
    })
    f.deleted = true
    g.touched = true
    rescore(g)
  } catch (e) {
    f.error = e.detail || `刪除失敗（${e.status}）`
  }
}

async function keepOne(g, keep) {
  const doomed = g.files.filter(f => f !== keep && !f.deleted)
  if (!doomed.length) return
  if (!confirm(`保留：\n  ${keep.name}\n\n刪除：\n` +
               doomed.map(f => '  ' + f.name).join('\n'))) return
  const paths = doomed.map(f => f.path)
  await releaseIfPlaying(paths)
  const r = await api('/api/media/delete', {
    method: 'POST', body: JSON.stringify({ paths }),
  })
  const gone = new Set(r.deleted)
  for (const f of doomed) if (gone.has(f.path)) f.deleted = true
  g.touched = true
  rescore(g)
  reportErrors(r.errors)
}

// Mean Hamming distance between the sampled frames, 0 = identical. Groups of
// three or more are where the grouping goes wrong (everything is compared to
// the first file, so unrelated videos chain together), so the batch action
// only ever touches pairs.
const t2Max = ref(4)

const t2Targets = computed(() => {
  const out = []
  for (const g of tier2Groups.value) {
    if (g.touched || !g.certain || !g.keeper) continue
    const live = g.files.filter(f => !f.deleted)
    if (live.length !== 2) continue
    if (g.distance === null || g.distance > t2Max.value) continue
    for (const f of live) if (f.path !== g.keeper) out.push({ g, f })
  }
  return out
})
const t2Bytes = computed(() => t2Targets.value.reduce((n, t) => n + t.f.size, 0))

async function executeTier2() {
  const targets = t2Targets.value
  if (!targets.length) return
  if (!confirm(
      `將刪除 ${targets.length} 個 Tier 2 重複檔案，釋出約 ${fmtSize(t2Bytes.value)}

` +
      `僅處理「剛好兩個檔案」且相似度距離 ≤ ${t2Max.value} 的組別。
` +
      `三個以上的組別容易誤判，一律排除。

` +
      `⚠ Tier 2 是「畫面相似」而非「檔案相同」，請確認你接受這個風險。`)) return

  executing.value = true
  try {
    const paths = targets.map(t => t.f.path)
    await releaseIfPlaying(paths)
    const r = await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ paths }),
    })
    const gone = new Set(r.deleted)
    for (const t of targets) {
      if (gone.has(t.f.path)) { t.f.deleted = true; t.g.touched = true }
    }
    for (const g of tier2Groups.value) rescore(g)
    reportErrors(r.errors)
  } catch (e) {
    alert('批次刪除失敗：' + errText(e))
  } finally { executing.value = false }
}

const executing = ref(false)

async function executeAuto() {
  const targets = autoTargets.value
  if (!targets.length) {
    alert('沒有可批次執行的項目。\n（Tier 2 不同畫質組別需人工個別確認）')
    return
  }
  const skip = []
  if (tier1.value.some(g => g.certain && g.touched)) skip.push('已手動處理的組')
  if (tier2Groups.value.length) skip.push(`${tier2Groups.value.length} 組 Tier 2 需人工確認`)
  if (!confirm(
      `將刪除 ${targets.length} 個 Tier 1 重複檔案，釋出約 ${fmtSize(autoBytes.value)}\n\n` +
      `（僅處理 Tier 1 有明確建議且未手動處理的組別）` +
      (skip.length ? `\n⚠ 已跳過：${skip.join('、')}` : '') + '\n\n確定執行？')) return

  executing.value = true
  try {
    const paths = targets.map(t => t.f.path)
    await releaseIfPlaying(paths)
    const r = await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ paths }),
    })
    const gone = new Set(r.deleted)
    for (const t of targets) {
      if (gone.has(t.f.path)) { t.f.deleted = true; t.g.touched = true }
    }
    for (const g of tier1.value) rescore(g)
    reportErrors(r.errors)
  } catch (e) {
    alert('批次刪除失敗：' + errText(e))
  } finally { executing.value = false }
}
</script>

<template>
  <div class="panel">
      <!-- collapsed scope summary -->
      <div v-if="!scopeOpen && !showHistory" class="barrow">
        <button class="scopebar" @click="reopenScope">
        <Icon name="chevronRight" :size="13" />
        範圍：待分類 {{ inboxOn }} · 歸檔 {{ charsOn }}
        <span v-if="tier2" class="t2tag">+ Tier 2</span>
        <span v-if="dedup.savedAt" class="oldtag">上次掃描 {{ fmtWhen(dedup.savedAt) }}</span>
        <span class="chg">重新掃描</span>
        </button>
        <button class="histbtn" title="之前的掃描報告" @click="showHistory = true">
          <Icon name="history" :size="13" /> 歷史紀錄</button>
      </div>

      <DupHistory v-if="showHistory" class="histpane" kind="video"
                  :current="dedup.historyId" @open="openHist"
                  @close="showHistory = false" />

      <div v-show="scopeOpen && !showHistory" class="scope optcard">
        <ScopePicker ref="picker" class="pick" storage-key="vam.dedup.excluded" :default-all="false"
                     :remember="false"
                     :disabled="dedup.running"
                     @update:selected="v => selected = v" />

        <div class="opts">
          <label class="opt">
            <input type="checkbox" v-model="tier2" :disabled="dedup.running" />
            同時掃描不同畫質的重複（Tier 2 — 時長 + 幀感知雜湊，較慢）
          </label>
          <label class="opt" :class="{ off: !tier2 }">
            時長容差
            <input type="number" v-model.number="threshold" min="0" max="30"
                   :disabled="dedup.running || !tier2" style="width:56px" /> 秒
          </label>
          <span class="spacer"></span>
          <button class="histbtn" :disabled="dedup.running" @click="showHistory = true">
            <Icon name="history" :size="13" /> 歷史紀錄</button>
          <button class="btn" :disabled="dedup.running || !selected.length" @click="scan">
            {{ dedup.running ? '掃描中…' : '開始掃描' }}
          </button>
        </div>
        <div class="tip">Tier 2 可找出不同畫質（720p vs 1080p）的同一影片，需讀取每部影片的幀，檔案多時耗時較長。</div>
      </div>

      <ScanProgress v-if="dedup.running" :progress="dedup.progress"
                    :stopping="dedup.stopping" @stop="stopScan" />
      <div v-if="error || dedup.error" class="err">{{ error || dedup.error }}</div>

      <!-- ── report ── -->
      <!-- Hidden while the picker is open. Both panes scroll, and with no
           priority between them the browser splits the height by content —
           an 84-group report left the 745-row folder list a visible sliver.
           You are either setting up a run or reviewing the last one. -->
      <!-- …and while the history is open, for the same reason: it was left a
           sliver beside a long report. -->
      <template v-if="dedup.groups && !dedup.running && !scopeOpen && !showHistory">
        <!-- A stopped scan looks exactly like a complete one that found little.
             Saying so is the difference between "沒有重複" and "還沒找完". -->
        <div v-if="dedup.partial" class="partial ib">
          <Icon name="warn" :size="14" />
          這份報告來自<b>中途停止</b>的掃描，只涵蓋已完成的部分；要完整結果請重新掃描。
        </div>
        <div v-if="!groups.length" class="empty ib">
          <Icon name="check" :size="15" />
          {{ dedup.partial ? '停止前尚未發現重複影片' : '未發現重複影片' }}</div>

        <template v-else>
          <div class="summary">
            發現 <b>{{ groups.length }}</b> 組重複　可釋出約 <b>{{ fmtSize(totalWaste) }}</b>　｜
            <span class="t1dot">●</span> Tier 1 <b>{{ tier1.length }}</b> 組
            <template v-if="tier2Groups.length">
              　<span class="t2dot">●</span> Tier 2（不同畫質）<b>{{ tier2Groups.length }}</b> 組
            </template>
            　自動建議 <b class="ok">{{ certainN }}</b> 組
            　需人工確認 <b class="warnc">{{ uncertainN }}</b> 組
          </div>

          <div class="legend">
            <span class="lg keep">■ 建議保留</span>
            <span class="lg del">■ 建議刪除</span>
            <span class="lg unsure">■ 需人工確認</span>
          </div>

          <!-- Without the delete permission every button below would only be
               refused by the server (/api/media/delete needs it): say so once
               instead of offering them. -->
          <div v-if="!can('delete')" class="noperm">這個帳號沒有「刪除檔案」的權限：可以看報告、播放比對，刪除要由有權限的帳號來做</div>
          <div v-else class="ctrl">
            <button class="btn exec ib" :disabled="!autoTargets.length || executing"
                    @click="executeAuto">
              <Icon name="check" :size="13" />
              執行 Tier 1 建議（{{ autoTargets.length }} 個檔案，釋出 {{ fmtSize(autoBytes) }}）
            </button>
            <button v-if="tier2Groups.length" class="btn exec t2exec ib"
                    :disabled="!t2Targets.length || executing" @click="executeTier2">
              <Icon name="check" :size="13" />
              執行 Tier 2 建議（{{ t2Targets.length }} 個檔案，釋出 {{ fmtSize(t2Bytes) }}）
            </button>
            <label v-if="tier2Groups.length" class="t2thr">
              相似度 ≤
              <input type="number" v-model.number="t2Max" min="0" max="20"
                     style="width:46px" />
              <span class="mini">僅雙檔組</span>
            </label>
            <button class="btn filt ib" :class="{ on: filter === 'manual' }"
                    :disabled="!manualN"
                    @click="filter = filter === 'manual' ? 'all' : 'manual'">
              <Icon name="warn" :size="13" /> 只看需人工確認（{{ manualN }} 組）
            </button>
            <!-- identical to the button above whenever every manual group is a
                 Tier 2 one, which is the common case; two buttons that filter
                 the same rows just look like a bug -->
            <button v-if="tier2Groups.length && tier2Groups.length !== manualN"
                    class="btn filt t2 ib" :class="{ on: filter === 'tier2' }"
                    @click="filter = filter === 'tier2' ? 'all' : 'tier2'">
              <Icon name="dot" :size="13" /> 只看 Tier 2（{{ tier2Groups.length }} 組）
            </button>
          </div>

          <div v-if="failed.length" class="failbar">
            <Icon name="warn" :size="14" />
            <span><b>{{ failed.length }}</b> 個檔案刪除失敗，各列後方有原因</span>
            <span class="spacer"></span>
            <button class="btn" :disabled="executing" @click="retryFailed">
              {{ executing ? '重試中…' : '重試全部失敗的' }}</button>
          </div>

          <div class="results" ref="resultsEl">
            <div v-for="(g, gi) in shown" :key="gi" class="card"
                 :class="{ active: viewed && g.files.includes(viewed) }">
              <div class="ghead">
                <span :class="g.tier === 2 ? 't2txt' : 't1txt'">
                  <span :class="g.tier === 2 ? 't2dot' : 't1dot'">●</span>
                  {{ g.tier === 2 ? 'Tier 2 · 不同畫質相似影片（請人工確認）'
                                  : 'Tier 1 · 檔案重複（大小/雜湊相符）' }}
                </span>
                <span v-if="g.tier === 2 && g.distance !== null" class="dist"
                      :class="{ far: g.distance > t2Max }"
                      title="取樣影格的平均漢明距離，0 = 完全相同">
                  距離 {{ g.distance }}</span>
                <span v-if="g.tier === 2 && g.files.filter(f => !f.deleted).length > 2"
                      class="manyfiles" title="三個以上的組別容易誤判，批次執行不會處理">
                  {{ g.files.filter(f => !f.deleted).length }} 檔</span>
                <span v-if="viewed && g.files.includes(viewed)" class="viewing ib">
                  <Icon name="eye" :size="12" /> 目前檢視中</span>
              </div>

              <template v-for="f in g.files" :key="f.path">
              <div class="row" :class="rowClass(g, f)">
                <span class="tag">[{{ f.label }}]</span>
                <span class="fn" :title="f.path">{{ f.name }}</span>
                <span class="col" :class="{ best: f.duration > 0 && f.duration === bestDur(g) }">
                  {{ f.duration > 0 && f.duration === bestDur(g) ? '★ ' : '' }}{{ fmtDur(f.duration) }}
                </span>
                <span class="col" :class="{ best: bestPx(g) > 0 && f.width * f.height === bestPx(g) }">
                  {{ bestPx(g) > 0 && f.width * f.height === bestPx(g) ? '★ ' : '' }}{{ f.width ? `${f.width}×${f.height}` : '?' }}
                </span>
                <span class="col sz">{{ fmtSize(f.size) }}</span>
                <span class="col dt">{{ fmtDate(f.mtime) }}</span>
                <button class="ib icon play" title="播放" :disabled="f.deleted"
                        @click="play(f)"><Icon name="play" :size="12" /></button>
                <template v-if="can('delete')">
                  <button class="ib icon trash" title="刪除此檔案" :disabled="f.deleted"
                          @click="delOne(g, f)"><Icon name="trash" :size="12" /></button>
                  <!-- deletes the others in the group -->
                  <button class="keepbtn" :disabled="f.deleted" @click="keepOne(g, f)">保留此項</button>
                </template>
              </div>
              <!-- whatever the OS actually said, verbatim. Must stay inside the
                   per-file loop: `f` does not exist outside it. -->
              <div v-if="f.error" class="rowerr ib">
                <Icon name="warn" :size="12" /> {{ f.error }}
              </div>
              </template>
            </div>
          </div>
        </template>
      </template>

    <!-- in-place player, so two copies can be compared without leaving the report -->
    <div v-if="playing" class="pmodal" @click.self="closePlayer">
      <div class="pbox">
        <div class="pname">{{ playing.name }}
          <button class="btn ghost" @click="closePlayer"><Icon name="close" :size="14" /></button>
        </div>
        <VideoPlayer :src="streamUrl(playing.path)" @close="closePlayer" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.barrow { display: flex; align-items: stretch; border-bottom: 1px solid #313148; }
.barrow .scopebar { flex: 1; border-bottom: none; }
.histbtn { background: none; border: none; border-left: 1px solid #313148;
  color: var(--dim); font-size: 12px; padding: 0 14px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.histbtn:hover { color: var(--text); }
.opts .histbtn { border: 1px solid #444; border-radius: 5px; padding: 5px 10px; }
.histpane { flex: 1; min-height: 0; overflow-y: auto; }
/* The modal shell lives in DupDialog.vue now. `contents` means these sections
   stay direct flex children of the dialog exactly as they were, so their
   full-bleed padding and dividing lines are unchanged and `.results` is still
   the one and only scroller. */
.panel { display: contents; }

.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 16px; background: none; border: none; border-bottom: 1px solid #313148;
  color: var(--dim); font-size: 12px; cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
.t2tag { color: #f9e2af; }
.oldtag { color: var(--dim); font-size: 11px; }

/* The options area is a card; the report below stays as it was.
   `.optcard`, not bare `.scope`: ScopePicker's root is a `.scope` too, and a
   parent's scoped rule reaches a child's root — it got a card of its own. */
.scope.optcard { margin: 4px 16px 8px; padding: 12px 14px 0; background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px;
  display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
/* Only the picker scrolls; the settings row with 開始掃描 stays in sight. The
   whole card used to scroll, which on a short window hid that row below the
   fold and made the user scroll for the one button they came for. */
.optcard .pick { flex: 1 1 auto; min-height: 0; overflow-y: auto; }

.opts { display: flex; align-items: center; gap: 16px; padding: 4px 0 10px;
  flex-wrap: wrap; flex-shrink: 0; }
.opt { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.opt.off { color: var(--dim); }
.spacer { flex: 1; }
.tip { color: var(--dim); font-size: 11px; padding: 0 0 12px; }

/* the running-scan block lives in ScanProgress.vue (scoped styles do not
   cross components, so it carries its own) */
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.err { padding: 12px 16px; color: var(--danger); font-size: 13px; }
.empty { color: var(--success); padding: 24px; justify-content: center; }
.partial { margin: 10px 16px 0; padding: 8px 12px; font-size: 12px;
  gap: 8px; color: #f9e2af; background: #f9e2af14;
  border: 1px solid #f9e2af44; border-radius: 5px; }
.partial b { color: #f9e2af; }

.summary { padding: 12px 16px 6px; font-size: 12px; }
.summary b { color: var(--text); }
.summary b.ok { color: var(--success); }
.summary b.warnc { color: #f9e2af; }
.t1dot { color: #f38ba8; }
.t2dot { color: #f9e2af; }
.t1txt { color: var(--dim); font-size: 11px; }
.t2txt { color: #f9e2af; font-size: 11px; }

.legend { display: flex; gap: 10px; padding: 0 16px 8px; }
.lg { font-size: 10px; border-radius: 3px; padding: 1px 8px; }
.lg.keep { background: #162416; }
.lg.del { background: #2a1616; }
.lg.unsure { background: #2a2010; }

.ctrl { display: flex; gap: 8px; padding: 0 16px 12px; flex-wrap: wrap; }
.noperm { margin: 0 16px 12px; color: var(--dim); font-size: 12px; }
.btn.exec { background: #1a3a1a; color: var(--success); border: 1px solid var(--success);
  font-size: 11px; }
.btn.exec:hover:not(:disabled) { background: #2a5a2a; }
.btn.filt { background: var(--card); color: #f9e2af; border: 1px solid #f9e2af;
  font-size: 11px; }
.btn.exec.t2exec { background: #2a2010; color: #f9e2af; border-color: #f9e2af; }
.btn.exec.t2exec:hover:not(:disabled) { background: #3a2a00; }
.t2thr { display: flex; align-items: center; gap: 5px; font-size: 11px;
  color: var(--dim); }
.t2thr .mini { font-size: 10px; }
.dist { margin-left: 8px; font-size: 10px; color: var(--success); }
.dist.far { color: var(--dim); }
.manyfiles { margin-left: 6px; font-size: 10px; color: var(--danger); }
.btn.filt.on { background: #3a2a00; }
.btn.filt.t2 { color: #f9e2af; }
.btn:disabled { opacity: .45; cursor: default; }

.results { overflow-y: auto; padding: 0 16px 16px; }
.card { border: 1px solid #404058; border-radius: 6px; margin-bottom: 10px;
  padding: 8px 10px; background: var(--card); }
.card.active { border: 2px solid var(--danger); padding: 7px 9px; }
.ghead { display: flex; align-items: center; margin-bottom: 4px; }
.viewing { margin-left: auto; color: var(--danger); font-size: 10px; font-weight: bold; }

.row { display: flex; align-items: center; gap: 6px; padding: 3px 6px;
  border-radius: 3px; margin-bottom: 2px; }
.row.keep { background: #162416; }
.row.del { background: #2a1616; }
.row.unsure { background: #2a2010; }
.row.gone { background: #1a1a1a; }
.failbar { display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: #f9e2af; background: #2a2010; border-radius: 6px;
  padding: 8px 10px; margin: 0 0 10px; }
.failbar .spacer { flex: 1; }
.rowerr { color: var(--danger); font-size: 10px; padding: 1px 6px 4px 142px;
  gap: 5px; }
.row.gone .fn { color: var(--dim); text-decoration: line-through; }
.tag { color: var(--accent); font-size: 10px; width: 130px; flex: 0 0 130px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fn { flex: 1; min-width: 0; font-size: 11px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.col { font-size: 10px; color: var(--dim); flex: 0 0 90px; text-align: left; }
.col.best { color: var(--success); }
.col.sz { flex-basis: 72px; }
.col.dt { flex-basis: 78px; }
.icon { width: 26px; height: 22px; border: none; border-radius: 3px;
  justify-content: center; cursor: pointer; }
.icon.play { background: #1a2a3a; color: #7eb8f7; }
.icon.trash { background: #5a2020; color: var(--danger); }
.icon:disabled { opacity: .3; cursor: default; }
.keepbtn { height: 22px; background: #1a3a1a; color: var(--success);
  border: 1px solid var(--success); border-radius: 3px; padding: 0 6px;
  font-size: 10px; cursor: pointer; }
.keepbtn:hover:not(:disabled) { background: #2a5a2a; }
.keepbtn:disabled { opacity: .3; cursor: default; }

.pmodal { position: fixed; inset: 0; background: rgba(0,0,0,.9);
  display: flex; align-items: center; justify-content: center; z-index: 90; }
.pbox { width: min(1000px, 94vw); }
.pname { display: flex; align-items: center; justify-content: space-between;
  color: var(--text); font-size: 13px; padding-bottom: 8px; }
</style>
