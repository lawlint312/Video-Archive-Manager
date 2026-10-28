<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, imageUrl, imageThumbUrl, errText } from '../api'
import { store, can } from '../store'
import { runScan, stopScan, loadSavedReport, rescore, isExactOnly,
         fmtSize, wmLabel } from '../imgdedup'
import { rememberPicking, wasPicking } from '../dedup'
import { openHistoryReport } from '../imgdedup'
import { batchPlan, batchRisk, BATCH_MAX } from '../imgbatch.js'
import ScanProgress from './ScanProgress.vue'
import ScopePicker from './ScopePicker.vue'
import Icon from './Icon.vue'
import DupHistory from './DupHistory.vue'


const d = store.imgdedup
const error = ref('')

// Same scope picker and same defaults as the video tab: nothing selected, and
// nothing remembered from last time.
// Open on the picker when there is no report, or when the user last left
// this report with "重新掃描" (see rememberPicking in dedup.js).
const scopeOpen = ref(!d.running && (d.groups === null || wasPicking('image')))
const showHistory = ref(false)

function reopenScope() {
  scopeOpen.value = true
  rememberPicking('image')
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

// what the collapsed summary reports, same as the video tab
const inboxOn = computed(() => picker.value?.inboxOn ?? 0)
const charsOn = computed(() => picker.value?.charsOn ?? 0)

function fmtWhen(t) {
  if (!t) return ''
  const dt = new Date(t * 1000)
  const pad = n => String(n).padStart(2, '0')
  return `${dt.getMonth() + 1}/${dt.getDate()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

const distance = ref(d.maxDistance)

async function start() {
  if (!selected.value.length) {
    error.value = '請至少選擇一個資料夾'
    return
  }
  error.value = ''
  scopeOpen.value = false
  filter.value = 'all'
  await runScan({ roots: selected.value, maxDistance: distance.value })
}

const groups = computed(() => d.groups || [])
const filter = ref('all')          // all | exact | similar | manual

const shown = computed(() => groups.value.filter(g => {
  if (g.files.filter(f => !f.deleted).length < 2) return false
  if (filter.value === 'exact') return isExactOnly(g)
  if (filter.value === 'similar') return !isExactOnly(g)
  if (filter.value === 'manual') return !g.certain
  return true
}))

const liveGroups = computed(() =>
  groups.value.filter(g => g.files.filter(f => !f.deleted).length > 1))
const exactGroups = computed(() => liveGroups.value.filter(isExactOnly))

const reclaimable = computed(() => {
  let n = 0
  for (const g of liveGroups.value) {
    const live = g.files.filter(f => !f.deleted)
    for (const f of live) if (f.path !== g.keeper) n += f.size
  }
  return n
})

// ── viewer ──────────────────────────────────────────────────────────────────
const viewing = ref(null)

// ── deleting ────────────────────────────────────────────────────────────────
const executing = ref(false)

async function delOne(g, f) {
  if (!confirm(`確定要永久刪除？\n\n${f.name}`)) return
  f.error = null
  try {
    if (viewing.value?.path === f.path) viewing.value = null
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

// The suggested keeper always first, so it sits at the left of its row and the
// eye compares every other picture against the same place. The rest keep the
// scan's order. Display only — `g.files` itself is left alone.
function keeperFirst(g) {
  const k = g.files.find(f => f.path === g.keeper)
  return k ? [k, ...g.files.filter(f => f !== k)] : g.files
}

async function keepOne(g, keep) {
  const doomed = g.files.filter(f => f !== keep && !f.deleted)
  if (!doomed.length) return
  if (!confirm(`保留：\n  ${keep.name}\n\n刪除：\n` +
               doomed.map(f => '  ' + f.name).join('\n'))) return
  await runDelete(doomed.map(f => f.path), [g])
}

// How far one 「執行建議」 reaches, chosen per report. Defaults to 0 (the same
// picture saved twice, plus byte-identical copies) and is deliberately not
// remembered: a 20 left over from last time would batch-delete pairs that are
// wrong about a third of the time without anyone choosing that today. The
// plan itself is in imgbatch.js; groups with no clear keeper are never in it.
const batchMax = ref(0)
const plan = computed(() => batchPlan(liveGroups.value, batchMax.value))
const risk = computed(() => batchRisk(batchMax.value))

async function executeBatch() {
  const p = plan.value
  if (!p.paths.length) return
  const warn = risk.value.level === 'safe' ? ''
    : `\n⚠ 門檻 ${batchMax.value}：${risk.value.text}，建議先逐組看過再批次。\n`
  if (!confirm(`將處理 ${p.groups.length} 組（相似距離 ≤ ${batchMax.value}），` +
               `每組保留建議的那張，刪除其餘 ${p.paths.length} 張（約 ${fmtSize(p.bytes)}）。\n` +
               warn +
               (p.undecided ? `\n另有 ${p.undecided} 組無法判斷該留哪張，不會處理。\n` : '') +
               `\n永久刪除，不會進資源回收筒。確定執行？`)) return
  await runDelete(p.paths, p.groups)
}

async function runDelete(paths, affected) {
  executing.value = true
  try {
    const r = await api('/api/media/delete', {
      method: 'POST', body: JSON.stringify({ paths }),
    })
    const gone = new Set(r.deleted)
    for (const g of affected) {
      for (const f of g.files) {
        if (gone.has(f.path)) { f.deleted = true; g.touched = true }
      }
      rescore(g)
    }
    for (const e of r.errors || []) {
      for (const g of affected) {
        const f = g.files.find(x => x.path === e.path)
        if (f) f.error = e.error
      }
    }
  } catch (e) {
    alert('刪除失敗：' + errText(e))
  } finally { executing.value = false }
}

// Same as the video tab: once the saved report is in, show it -- unless the
// user left it with "重新掃描". (This tab used to stay on the picker after a
// page reload even with a report on the server.)
onMounted(async () => {
  await loadSavedReport()
  if (d.groups?.length && !wasPicking('image')) scopeOpen.value = false
})
</script>

<template>
  <div class="panel">
      <!-- ── scope ──
           Collapsed it is a one-line summary of what is selected; expanded it
           IS the picker. There is no "選擇範圍" button, for the same reason the
           JAV picker lost its "選擇角色" one: it was never a dropdown — the
           list was already open below it — so the label described a screen
           that does not exist. The video tab has worked this way all along. -->
      <div v-if="!scopeOpen && !showHistory" class="barrow">
        <button class="scopebar" @click="reopenScope">
        <Icon name="chevronRight" :size="13" />
        範圍：待分類 {{ inboxOn }} · 歸檔 {{ charsOn }}
        <span class="thrtag">門檻 {{ distance }}</span>
        <span v-if="d.savedAt" class="oldtag">上次掃描 {{ fmtWhen(d.savedAt) }}</span>
        <span class="chg">重新掃描</span>
        </button>
        <button class="histbtn" title="之前的掃描報告" @click="showHistory = true">
          <Icon name="history" :size="13" /> 歷史紀錄</button>
      </div>

      <DupHistory v-if="showHistory" class="histpane" kind="image"
                  :current="d.historyId" @open="openHist"
                  @close="showHistory = false" />

      <div v-show="scopeOpen && !showHistory" class="scope optcard">
        <ScopePicker ref="picker" class="pick" storage-key="vam.imgdedup.scope"
                     :default-all="false" :remember="false" :disabled="d.running"
                     @update:selected="selected = $event" />

        <!-- One row, and 開始掃描 sits at its right end — same as the video
             tab. It used to be a row of its own below the threshold, so it
             read as a separate step rather than as "go" for these settings. -->
        <div class="opts">
          <label class="opt thr">
            相似門檻
            <input type="range" min="0" max="30" v-model.number="distance"
                   :disabled="d.running" />
            <b>{{ distance }}</b> / 256
          </label>
          <span class="hint">
            {{ distance <= d.safeDistance ? '嚴格（實測無誤判）'
               : distance <= 19 ? '中等（約 1 成需人工確認）'
               : '寬鬆（約 3 成需人工確認）' }}
          </span>
          <span class="spacer"></span>
          <button class="histbtn" :disabled="d.running" @click="showHistory = true">
            <Icon name="history" :size="13" /> 歷史紀錄</button>
          <button class="btn" :disabled="d.running || !selected.length"
                  @click="start">
            {{ d.running ? '掃描中…' : '開始掃描' }}
          </button>
        </div>
        <div v-if="error" class="err">{{ error }}</div>
      </div>

      <ScanProgress v-if="d.running" :progress="d.progress"
                    :stopping="d.stopping" @stop="stopScan" />
      <div v-if="d.error" class="err big">{{ d.error }}</div>
      <div v-if="d.partial && !d.running && groups.length && !scopeOpen && !showHistory"
           class="partial ib">
        <Icon name="warn" :size="14" />
        這份報告來自<b>中途停止</b>的掃描，只涵蓋已完成的部分；要完整結果請重新掃描。
      </div>

      <!-- ── report ── -->
      <!-- Hidden while the picker is open. Both panes scroll, and with no
           priority between them the browser splits the height by content —
           an 84-group report left the 745-row folder list a visible sliver.
           You are either setting up a run or reviewing the last one. -->
      <!-- …and while the history is open: it was left a sliver beside a long
           report, the list unreadable. -->
      <template v-if="groups.length && !scopeOpen && !showHistory">
        <div class="bar">
          <div class="filters">
            <button class="chip" :class="{ on: filter === 'all' }"
                    @click="filter = 'all'">全部 {{ liveGroups.length }}</button>
            <button class="chip" :class="{ on: filter === 'exact' }"
                    @click="filter = 'exact'">完全相同 {{ exactGroups.length }}</button>
            <button class="chip" :class="{ on: filter === 'similar' }"
                    @click="filter = 'similar'">視覺相似
              {{ liveGroups.length - exactGroups.length }}</button>
            <button class="chip" :class="{ on: filter === 'manual' }"
                    @click="filter = 'manual'">需人工判斷</button>
          </div>
          <span class="spacer"></span>
          <span class="hint">可釋出約 {{ fmtSize(reclaimable) }}</span>
        </div>
        <!-- the batch: how far it reaches is the user's call, every time.
             Without the delete permission it, and every delete below, would
             only be refused by the server: say so once instead. -->
        <div v-if="!can('delete')" class="batch noperm">這個帳號沒有「刪除檔案」的權限：可以看報告、播放比對，刪除要由有權限的帳號來做</div>
        <div v-else class="batch">
          <label class="opt thr">
            批次門檻
            <input type="range" min="0" :max="BATCH_MAX" v-model.number="batchMax"
                   :disabled="executing" />
            <b>{{ batchMax }}</b>
          </label>
          <span class="risk" :class="risk.level">{{ risk.text }}</span>
          <span class="hint">
            {{ plan.groups.length }} 組 · 刪除 {{ plan.paths.length }} 張 · 約 {{ fmtSize(plan.bytes) }}<template
              v-if="plan.undecided">（另有 {{ plan.undecided }} 組無法判斷，不處理）</template>
          </span>
          <span class="spacer"></span>
          <button class="btn sm" :disabled="executing || !plan.paths.length"
                  @click="executeBatch">
            {{ executing ? '刪除中…' : `執行建議（${plan.groups.length} 組）` }}
          </button>
        </div>

        <div class="results">
          <div v-for="(g, gi) in shown" :key="gi" class="grp"
               :class="{ touched: g.touched }">
            <div class="ghead">
              <span class="tag" :class="isExactOnly(g) ? 't1' : 't2'">
                {{ isExactOnly(g) ? '完全相同' : `相似 ${g.distance}/256` }}
              </span>
              <span v-if="g.folder" class="folder">{{ g.folder }}</span>
              <span v-if="!g.certain" class="warn">無法判斷該留哪張</span>
              <span class="spacer"></span>
              <span class="hint">{{ g.files.filter(f => !f.deleted).length }} 張</span>
            </div>
            <div class="shots">
              <template v-for="f in keeperFirst(g)" :key="f.path">
                <div v-if="!f.deleted" class="shot"
                     :class="{ keep: f.path === g.keeper }">
                  <img :src="imageThumbUrl(f.path)" loading="lazy"
                       @click="viewing = f" />
                  <!-- On the picture, not in the button row: the card is 190px
                       wide and the badge used to take its width from the two
                       buttons beside it, so the keeper's buttons wrapped and
                       came out a different size from every other card's. -->
                  <span v-if="f.path === g.keeper" class="badge">建議保留<template
                        v-if="g.why"> · {{ g.why }}</template></span>
                  <div class="meta">
                    <div class="nm" :title="f.path">{{ f.name }}</div>
                    <div class="sm">
                      {{ f.width }}×{{ f.height }} · {{ fmtSize(f.size) }}
                      <span class="lab">{{ f.label }}</span>
                    </div>
                    <!-- what the scan measured, so a wrong call is visible
                         on the card instead of only in the suggestion -->
                    <div v-if="wmLabel(f)" class="wm"
                         :class="{ clean: wmLabel(f) === '無浮水印' }">{{ wmLabel(f) }}</div>
                    <div v-if="f.error" class="err">{{ f.error }}</div>
                  </div>
                  <div v-if="can('delete')" class="acts">
                    <button class="btn ghost xs" @click="keepOne(g, f)">留這張</button>
                    <button class="btn xs del" @click="delOne(g, f)">刪除</button>
                  </div>
                </div>
              </template>
            </div>
          </div>
          <div v-if="!shown.length" class="empty">這個篩選沒有項目。</div>
        </div>
      </template>
      <!-- Not under the picker: it took the room the settings row needed and
           pushed 開始掃描 out of sight. -->
      <div v-else-if="!d.running && d.groups !== null && !scopeOpen && !showHistory"
           class="empty">
        沒有發現重複的圖片。
      </div>

    <!-- full-size look, because a thumbnail cannot settle "are these the same?" -->
    <div v-if="viewing" class="viewer" @click="viewing = null">
      <img :src="imageUrl(viewing.path)" />
      <div class="vname">{{ viewing.name }} · {{ viewing.width }}×{{ viewing.height }}
        · {{ fmtSize(viewing.size) }}</div>
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
/* The modal shell lives in DupDialog.vue now, and this panel is laid out
   exactly like the video one: `display: contents`, so every section is a
   direct flex child of the dialog and carries its own full-bleed 16px.
   It was briefly a padded box instead, which nested two insets (the panel's
   16px plus `.scope`'s own border and 10px) and made the two tabs visibly
   different widths — the folder list sat further from the frame on this one.
   Same numbers as VideoDupPanel now, so switching tabs moves nothing. */
.panel { display: contents; }
.spacer { flex: 1; }
.hint { color: var(--muted); font-size: 12px; }
/* The collapsed summary: what is selected, not an offer to open a list that
   was already open. */
.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 16px; background: none; border: none;
  border-bottom: 1px solid #313148;
  color: var(--dim); font: inherit; font-size: 12px;
  cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
.thrtag { color: #f9e2af; }
.oldtag { color: var(--dim); font-size: 11px; }
/* The options area is a card; the report below stays as it was.
   `.optcard`, not bare `.scope`: ScopePicker's root is a `.scope` too, and a
   parent's scoped rule reaches a child's root — it got a card of its own. */
.scope.optcard { margin: 4px 16px 8px; padding: 12px 14px 0; background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px;
  display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
/* Only the picker scrolls; the settings row stays in sight (VideoDupPanel). */
.optcard .pick { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
/* One settings row with 開始掃描 at its right end — the same shape as the
   video tab's `.opts`. */
.opts { display: flex; align-items: center; gap: 16px; padding: 4px 0 10px;
  flex-wrap: wrap; flex-shrink: 0; }
.opt { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.thr input { width: 160px; }
/* the running-scan block is ScanProgress.vue; scoped styles do not cross */
.partial { margin: 10px 16px 0; padding: 8px 12px; font-size: 12px; gap: 8px;
  color: #f9e2af; background: #f9e2af14; border: 1px solid #f9e2af44;
  border-radius: 5px; }
.partial b { color: #f9e2af; }
.err { padding: 0 16px; color: var(--danger, #e66); font-size: 12px; }
.err.big { padding: 12px 16px; font-size: 13px; }
.bar { display: flex; align-items: center; gap: 10px; padding: 12px 16px 6px; }
.filters { display: flex; gap: 6px; }
/* its own row under the filters: slider, how risky that distance is, and what
   the button will actually do — read before pressing, not in the confirm */
.batch { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 2px 16px 10px; }
.risk { font-size: 12px; padding: 1px 8px; border-radius: 999px; }
.risk.safe { color: #a6e3a1; background: #a6e3a118; }
.risk.mid { color: #f9e2af; background: #f9e2af18; }
.risk.high { color: #f38ba8; background: #f38ba818; }
.chip { background: transparent; border: 1px solid var(--line); color: var(--text);
  border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.results { overflow-y: auto; padding: 0 16px 16px;
  display: flex; flex-direction: column; gap: 10px; }
.grp { border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
.grp.touched { opacity: .65; }
.ghead { display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
  font-size: 12px; }
.tag { border-radius: 4px; padding: 1px 7px; font-size: 11px; }
.tag.t1 { background: #2c6; color: #042; }
.tag.t2 { background: #c93; color: #321; }
.folder { color: var(--muted); }
.warn { color: #e94; }
.shots { display: flex; gap: 10px; flex-wrap: wrap; }
.shot { width: 190px; border: 1px solid var(--line); border-radius: 6px;
  padding: 6px; display: flex; flex-direction: column; gap: 4px;
  position: relative; }
.shot.keep { border-color: var(--accent); }
.shot img { width: 100%; height: 150px; object-fit: contain; background: #000;
  border-radius: 4px; cursor: zoom-in; }
.nm { font-size: 11px; word-break: break-all; }
.sm { font-size: 11px; color: var(--muted); }
.lab { margin-left: 4px; }
.wm { font-size: 11px; color: #e94; }
.wm.clean { color: var(--success); }
.acts { display: flex; align-items: center; gap: 4px; margin-top: auto; }
.noperm { color: var(--dim); font-size: 12px; }
.acts .btn { flex: 1; white-space: nowrap; }
/* The video panel's trash colours. This was `btn danger`, but `.danger` is
   defined nowhere, so it fell back to plain `.btn` — the brightest button on
   the page, on the one action that cannot be undone. */
.acts .btn.del { background: #5a2020; color: var(--danger); }
.acts .btn.del:hover { background: #7a2a2a; }
.badge { position: absolute; top: 10px; left: 10px; pointer-events: none;
  background: var(--accent); color: #fff; border-radius: 4px;
  padding: 2px 6px; font-size: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.5); }
.empty { color: var(--muted); font-size: 13px; padding: 24px 16px;
  text-align: center; }
.viewer { position: fixed; inset: 0; background: rgba(0,0,0,.9); z-index: 60;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
.viewer img { max-width: 96vw; max-height: 90vh; max-height: 90dvh; object-fit: contain; }
.vname { position: absolute; bottom: 14px; color: #fff; font-size: 12px;
  background: rgba(0,0,0,.6); padding: 4px 10px; border-radius: 6px; }
</style>
