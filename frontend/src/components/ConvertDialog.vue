<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api, errText, streamUrl } from '../api'
import { store, setStatus, setBusy, can } from '../store'
import { followScan, stopScan as stopScanJob, takeResult } from '../convertscan'
import { fmtSize, fmtDur } from '../dedup'
import { convertWarning } from '../convertwarn'
import ScopePicker from './ScopePicker.vue'
import ShrinkPanel from './ShrinkPanel.vue'
import Icon from './Icon.vue'
import VideoPlayer from './VideoPlayer.vue'
import DupHistory from './DupHistory.vue'
import SideDialog from './SideDialog.vue'

const emit = defineEmits(['close', 'done'])

// two jobs live in this dialog: 'convert' makes files playable,
// 'shrink' makes them smaller. They share only the shell.
// Two separate ticks (轉檔、修復 / 壓縮): open on whichever this account has.
const tab = ref(can('convert') ? 'convert' : 'shrink')
const TABS = computed(() => [
  can('convert') && { key: 'convert', icon: 'refresh', label: '轉檔為 MP4' },
  can('shrink') && { key: 'shrink', icon: 'gauge', label: '壓縮',
                      heading: '壓縮（降低畫質換空間）' },
].filter(Boolean))

const selected = ref([])
// Remembered: someone who has once discovered that .mp4 is not a promise
// wants the check every time, and it is the slow half of the scan so the
// choice matters enough to persist.
const includeMp4 = ref(localStorage.getItem('vam.convert.mp4') === '1')
watch(includeMp4, v => localStorage.setItem('vam.convert.mp4', v ? '1' : '0'))
// Which explanation is open, or null. One at a time — two open at once
// just pushes the second out of view (same rule as the settings help).
const help = ref(null)
function toggleHelp(k) { help.value = help.value === k ? null : k }
const checkTiming = ref(localStorage.getItem('vam.convert.timing') === '1')
watch(checkTiming, v => localStorage.setItem('vam.convert.timing', v ? '1' : '0'))
// Not remembered, unlike the two above: it costs about 4 s a file and pins
// the CPU, so it should be a deliberate choice each time, made with a small
// scope -- not something that silently rides along on the next big scan.
const checkDecode = ref(false)
// Recordings with long stretches of nothing (the stream stopped sending, the
// recorder kept its clock). Cheap -- it reads only the packet index -- but a
// different errand, so it is asked for, and like the decode check it is not
// remembered.
const checkBlanks = ref(false)
// Every check remembers its answer per file and a file unchanged since is not
// checked again. This ignores that, for the scan it is ticked on. Not
// remembered: left on, it would quietly make every later scan slow again.
const recheck = ref(false)
// How many files each check reused this time (only for a scan just run).
const reusedChecks = ref(null)
const CHECK_NAMES = { probe: '格式', timing: '影格時序', decode: '完整解碼',
                      blanks: '錄影中斷' }
const reusedText = computed(() => {
  const c = reusedChecks.value || {}
  return Object.keys(CHECK_NAMES)
    .filter(k => c[k]?.reused)
    .map(k => `${CHECK_NAMES[k]} ${c[k].reused.toLocaleString()}／${
      (c[k].reused + c[k].checked).toLocaleString()} 支`)
    .join('・')
})
// The scan itself is followed outside this dialog (convertscan.js), so the
// status bar keeps going when it is closed and reopening finds the same scan.
const cs = store.convertScan
const stopped = ref(false)     // the result on screen is partial: say so
const viewing = ref(null)      // a damaged file being looked at
// Where its damage is, located when it is opened: on a long film nobody finds
// a one-second glitch by watching. null = locating, [] = none found.
const regions = ref(null)
const locateErr = ref('')
const player = ref(null)
const LEAD_IN = 3
watch(viewing, async v => {
  regions.value = null
  locateErr.value = ''
  if (!v || !(v.decode_errors || v.audio_errors)) return
  if (v.regions) { regions.value = v.regions; return }   // the scan knew already
  const at = v.path
  try {
    const r = await api(`/api/convert/locate?path=${encodeURIComponent(at)}`)
    if (viewing.value?.path !== at) return     // another file opened meanwhile
    regions.value = r.regions || []
    v.regions = regions.value                 // the row can show it too
  } catch (e) {
    if (viewing.value?.path === at) locateErr.value = errText(e)
  }
})
function jump(g) {
  const el = player.value?.el
  if (!el) return
  el.currentTime = Math.max(0, g.start - LEAD_IN)
  el.play().catch(() => {})
}
// A stretch is a range — "0:27～2:07" says a minute and a half has no
// picture, which "0:27" alone hid. Under a second shows as a single time.
function span(g) {
  return g.end - g.start >= 1 ? `${mmss(g.start)}～${mmss(g.end)}` : mmss(g.start)
}
function mmss(t) {
  t = Math.max(0, Math.floor(t))
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60
  const p = n => String(n).padStart(2, '0')
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`
}
const scopeOpen = ref(true)
// Saved scans (backend: /api/convert/history). A scan with the decode check
// can run for an hour, and its result used to live only in this dialog.
const showHistory = ref(false)
const historyId = ref(null)     // the saved scan on screen, if it came from one
const historyAt = ref(null)
const lastScan = ref(null)      // newest saved scan, offered on the picker
// Stops the run poll of a dialog that was closed (a run cannot be closed on,
// but a page navigation can still unmount it).
let alive = true
onUnmounted(() => { alive = false })
// the scan's own options, so the stop button and the messages match it
function showOptions(o) {
  o = o || {}
  includeMp4.value = !!o.include_mp4
  checkTiming.value = !!o.check_timing
  checkDecode.value = !!o.check_decode
  checkBlanks.value = !!o.check_blanks
  recheck.value = !!o.recheck
}
onMounted(async () => {
  if (cs.result) {                 // finished while the dialog was closed
    showOptions(cs.options)
    applyScan(takeResult())
  } else if (cs.jobId) {           // still running: already being followed
    showOptions(cs.options)
  } else {
    // A page reload forgets the job; the server still knows this user's.
    try {
      const { scan } = await api('/api/convert/scan/active')
      if (scan && alive && !cs.jobId) {
        showOptions(scan.options)
        followScan(scan.job_id, scan.options, scan.progress || '掃描中…',
                   !!scan.stopping)
      }
    } catch { /* an older backend: start a new scan as before */ }
  }
  try {
    const r = await api('/api/convert/history?kind=scan')
    lastScan.value = (r.entries || [])[0] || null
  } catch { /* the button still works */ }
})
function preselect(rows) {
  // remux is free and lossless; everything else stays opt-in
  chosen.value = new Set(rows.filter(i => i.action === 'remux' ||
                                          i.action === 'remux_audio')
                             .map(i => i.path))
}
async function openHistory(id) {
  error.value = ''
  try {
    const r = await api(`/api/convert/history/scan/${id}`)
    items.value = r.rows || []
    result.value = null
    reusedChecks.value = null
    stopped.value = !!r.partial
    historyId.value = id
    historyAt.value = r.at
    preselect(items.value)
    showHistory.value = false
    scopeOpen.value = false
  } catch (e) { error.value = '開啟紀錄失敗：' + errText(e) }
}
function whenShort(t) {
  if (!t) return ''
  const d = new Date(t * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`
}
const scanning = computed(() => !!cs.jobId)
const running = ref(false)
const progress = ref('')
const pct = ref(0)
const error = ref('')
const items = ref(null)        // null = not scanned yet
const result = ref(null)

const crf = ref(20)
const gpu = ref(false)

// remux is lossless and fast; re-encoding is neither, so it is opt-in per file
const chosen = ref(new Set())

const remuxable = computed(() =>
  (items.value || []).filter(i => i.action === 'remux' || i.action === 'remux_audio'))
const reencodable = computed(() =>
  (items.value || []).filter(i => i.action === 'reencode'))
// Files ffmpeg cannot open at all, but which turn out to be MP4s under the
// wrong extension with a damaged fragment. Opt-in, and unlike every other
// action here it does not delete the original.
const repairable = computed(() =>
  (items.value || []).filter(i => i.action === 'repair'))
// Playable codec, correct container, and the browser still drops about half
// the frames — the MP4 has no composition times for a stream that uses
// B-frames. Lossless to fix, but it needs GPAC, so the scan only looks when
// asked. See backend/app/core/retime.py.
const retimeable = computed(() =>
  (items.value || []).filter(i => i.action === 'retime'))
// Files that freeze the browser's player but are fixable, both found by the
// full decode check: corrupt audio packets (audiofix: video copied, audio
// re-encoded), and the truncated frame before each recording dropout
// (gapfix: those frames dropped, nothing re-encoded). Chrome fails the whole
// player on the first bad packet of either kind. Opt-in like the retime,
// since both replace the original.
const audiofixable = computed(() =>
  (items.value || []).filter(i => i.action === 'audiofix' || i.action === 'gapfix'))
// Recordings that stopped receiving for a while: the blanks come out by
// rewriting timestamps, every frame copied. See convert.blank_plan.
const blankcuttable = computed(() =>
  (items.value || []).filter(i => i.action === 'gapcut'))
const skipped = computed(() =>
  (items.value || []).filter(i => i.action === 'skip'))
// Picture data damaged -- found only by decoding every frame. Nothing here
// can fix that, so these are listed, never picked; the remedies are the
// delete mode and the clip tool. Worst first.
const damaged = computed(() =>
  (items.value || []).filter(i => i.action === 'damaged')
    .sort((a, b) => b.decode_errors - a.decode_errors))

const picked = computed(() =>
  [...remuxable.value, ...reencodable.value, ...repairable.value,
   ...retimeable.value, ...audiofixable.value, ...blankcuttable.value,
   ...damaged.value]
    .filter(i => chosen.value.has(i.path)))
const pickedBytes = computed(() => picked.value.reduce((n, i) => n + i.size, 0))
const anyReencode = computed(() => picked.value.some(i => i.action === 'reencode'))
const anyRepair = computed(() => picked.value.some(i => i.action === 'repair'))
const anyRetime = computed(() => picked.value.some(i => i.action === 'retime'))

function toggle(i) {
  const s = new Set(chosen.value)
  s.has(i.path) ? s.delete(i.path) : s.add(i.path)
  chosen.value = s
}
function setAll(list, on) {
  const s = new Set(chosen.value)
  for (const i of list) on ? s.add(i.path) : s.delete(i.path)
  chosen.value = s
}

async function poll(jobId, url, onProgress) {
  while (true) {
    await new Promise(r => setTimeout(r, 600))
    if (!alive) throw { detail: '對話框已關閉' }
    const j = await api(`${url}/${jobId}`)
    onProgress(j.progress)
    if (j.status === 'running') continue
    if (j.status === 'error') throw { detail: j.error || '執行失敗' }
    // Anything that is not running and not an error is a finished job. Testing
    // for 'done' by name meant a status this loop had not been taught about
    // (a 'cancelled' was added to the job runner later) would poll for ever
    // with no way out and no message.
    if (j.result == null) throw { detail: `工作結束但沒有結果（${j.status}）` }
    return j.result
  }
}

async function scan() {
  if (!selected.value.length) { error.value = '請至少選擇一個資料夾'; return }
  stopped.value = false
  error.value = ''
  items.value = null
  result.value = null
  const options = { include_mp4: includeMp4.value,
                    check_timing: checkTiming.value,
                    check_decode: checkDecode.value,
                    check_blanks: checkBlanks.value,
                    recheck: recheck.value }
  try {
    const { job_id } = await api('/api/convert/scan', {
      method: 'POST',
      body: JSON.stringify({ roots: selected.value, ...options }),
    })
    followScan(job_id, options, '啟動中…')
  } catch (e) {
    error.value = '掃描失敗：' + errText(e)
  }
}

// The followed scan ended while this dialog is open.
watch(() => cs.result, r => { if (r) applyScan(takeResult()) })
watch(() => cs.error, e => {
  if (e) { error.value = '掃描失敗：' + e; cs.error = '' }
})

function applyScan(out) {
  if (!out) return
  stopped.value = !!cs.stopping       // a stopped scan returns what it found
  cs.stopping = false
  // a backend from before the reuse counts returned the bare list
  const res = Array.isArray(out) ? out : (out.rows || [])
  reusedChecks.value = Array.isArray(out) ? null : out.checks
  items.value = res
  result.value = null
  // pre-select everything that can be remuxed — that path is free.
  // Re-encoding is lossy and repair is a guess at damaged data, so both
  // stay opt-in.
  preselect(res)
  historyId.value = null
  historyAt.value = null
  scopeOpen.value = false
}

// Only the decode pass reads the flag; it finishes the files in hand and
// returns what it found so far.
async function stopScan() {
  try { await stopScanJob() }
  catch (e) { error.value = '無法停止：' + errText(e) }
}

async function run() {
  if (!picked.value.length) return
  // The text lives in src/convertwarn.js so it can be tested — nothing here
  // is ever executed by a test, which is how a ReferenceError in this function
  // shipped as "開始轉檔 沒反應".
  if (!confirm(convertWarning(picked.value))) return

  running.value = true
  error.value = ''
  result.value = null
  pct.value = 0
  try {
    const { job_id } = await api('/api/convert/run', {
      method: 'POST',
      body: JSON.stringify({
        // a damaged row is listed as what it is; what gets run on it is the
        // concealing re-encode
        items: picked.value.map(i => ({ path: i.path,
                                        action: i.action === 'damaged' ? 'conceal'
                                                                        : i.action })),
        crf: crf.value, gpu: gpu.value,
      }),
    })
    const res = await poll(job_id, '/api/convert/job', p => {
      if (p && typeof p === 'object') {
        pct.value = Math.round((p.pct || 0) * 100)
        // A retime is three passes, not one encode, so the stage is the only
        // thing that says what it is doing — without it the bar sat at 0%
        // for 83 seconds and then jumped to done.
        const stage = p.stage ? ` · ${p.stage}` : ''
        progress.value = `(${p.index}/${p.total}) ${p.name}${stage} — ${pct.value}%`
        setBusy(`轉檔中 ${progress.value}`)
      }
    })
    result.value = res
    setStatus(`轉檔完成：${res.converted.length} 個成功` +
              (res.failed.length ? `，${res.failed.length} 個失敗` : '') +
              (res.saved > 0 ? `，省下 ${fmtSize(res.saved)}` : ''),
              res.failed.length ? 'warn' : 'ok')
    // drop the finished rows so a second run does not offer them again
    const done = new Set(res.converted.map(c => c.src))
    items.value = items.value.filter(i => !done.has(i.path))
    chosen.value = new Set([...chosen.value].filter(p => !done.has(p)))
    emit('done')
  } catch (e) {
    error.value = '轉檔失敗：' + errText(e)
  } finally { running.value = false; setBusy('') }
}

// minutes:seconds, or h:mm:ss -- the blanks are minutes long
function fmtLen(sec) {
  const t = Math.max(0, Math.round(sec || 0))
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60
  const p = n => String(n).padStart(2, '0')
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`
}

function label(a) {
  return a === 'remux' ? '換殼'
       : a === 'remux_audio' ? '換殼＋音訊'
       : a === 'reencode' ? '重編碼'
       : a === 'repair' ? '修復'
       : a === 'retime' ? '修正時序'
       : a === 'audiofix' ? '修復聲音'
       : a === 'gapfix' ? '修復斷訊'
       : a === 'gapcut' ? '去除空白'
       : a === 'conceal' ? '遮蔽損壞'
       : a === 'damaged' ? '損壞' : '略過'
}
</script>

<template>
  <!-- Locked while converting: closing would lose the progress, and the
       pages share the one running job's state -->
  <SideDialog title="影片處理" :items="TABS" v-model="tab"
              :locked="running" width="1300px" height="1000px" pad="16px" @close="emit('close')">

      <ShrinkPanel v-if="tab === 'shrink'" />
      <!-- Same scrolling arrangement as ShrinkPanel: the dialog is capped at
           90vh, so the tab body has to be the scroller or a long result list
           pushes everything under it out of reach. -->
      <div v-else class="tabbody">

      <div v-if="!scopeOpen && !showHistory" class="barrow">
        <button class="scopebar" @click="scopeOpen = true">
          <Icon name="chevronRight" :size="13" />
          <template v-if="historyId">歷史紀錄：{{ whenShort(historyAt) }} 的掃描（已處理的不再列出）</template>
          <template v-else>範圍：已選 {{ selected.length }} 個資料夾</template>
          <span class="chg">重新選擇</span>
        </button>
        <button class="histbtn ib" :disabled="running" title="之前的掃描結果"
                @click="showHistory = true"><Icon name="history" :size="13" /> 歷史紀錄</button>
      </div>
      <DupHistory v-if="showHistory" class="histpane" kind="scan"
                  endpoint="/api/convert/history" unit="個" :current="historyId"
                  @open="openHistory" @close="showHistory = false" />

      <div v-show="scopeOpen && !showHistory" class="scopewrap">
        <div v-if="lastScan && lastScan.remaining && !items" class="lastscan ib">
          <Icon name="history" :size="13" />
          上次掃描（{{ whenShort(lastScan.at) }}）還有 {{ lastScan.remaining }} 個待處理
          <button class="lnk" :disabled="scanning" @click="openHistory(lastScan.id)">開啟</button>
          <span class="spacer"></span>
          <button class="lnk" :disabled="scanning" @click="showHistory = true">全部歷史紀錄</button>
        </div>
        <!-- `remember: false` like both scan dialogs. Convert used to keep the
             selection, and the reasoning for that ("you pick a target and come
             back to it") loses to the risk: this dialog deletes and replaces
             files, and a folder still ticked from last time is invisible once
             the picker is collapsed. -->
        <ScopePicker storage-key="vam.convert.picked" :default-all="false"
                     :remember="false" :disabled="scanning || running"
                     @update:selected="v => selected = v" />
        <div class="opts">
          <!-- Off by default because it is the slow half: it has to ffprobe
               every MP4 in the library, and 95.7% of them turn out to be plain
               h264. Worth offering because the extension is not evidence -- an
               .mp4 holding mpeg4 video plays in no browser, and one such file
               was invisible to this tool entirely. -->
          <!-- Stacked in their own column so the scan button keeps its one
               line; the long explanations live behind the "?". -->
          <div class="chks">
          <label class="chk">
            <input type="checkbox" v-model="includeMp4"
                   :disabled="scanning || running" />
            同時檢查 .mp4 是否真的能播（全庫約 3～4 分鐘）
          </label>
          <!-- A separate switch rather than riding on the one above, because
               they are different errands: that one asks "can the browser
               decode this", this one asks "will it play smoothly". Only
               meaningful for MP4s, so it follows that checkbox. -->
          <label class="chk sub" v-if="includeMp4">
            <input type="checkbox" v-model="checkTiming"
                   :disabled="scanning || running" />
            並檢查影格時序（再慢約 3 倍）
          </label>
          <!-- Independent of the two above: it decodes every video in scope,
               .mp4 or not, because the damaged ones are usually ordinary
               MP4s that pass every other check. -->
          <label class="chk">
            <input type="checkbox" v-model="checkDecode"
                   :disabled="scanning || running" />
            完整解碼檢查，找出畫面損壞的影片（每支約 4 秒，建議只選少數資料夾）
          </label>
          <label class="chk">
            <input type="checkbox" v-model="checkBlanks"
                   :disabled="scanning || running" />
            檢查錄影中斷，找出中間一大段沒有畫面也沒有聲音的錄影（每支約 1 秒）
          </label>
          <label class="chk">
            <input type="checkbox" v-model="recheck"
                   :disabled="scanning || running" />
            全部重新檢查（不沿用上次的結果）
          </label>
          </div>
          <button class="qmark" :class="{ on: help === 'scan' }"
                  title="這個掃描會做什麼" @click="toggleHelp('scan')">?</button>
          <button class="lnk histlnk ib" :disabled="scanning || running"
                  title="之前的掃描結果" @click="showHistory = true">
            <Icon name="history" :size="12" /> 歷史紀錄</button>
          <button class="btn scanbtn" :disabled="scanning || running || !selected.length"
                  @click="scan">{{ scanning ? '掃描中…'
                    : checkDecode ? '掃描並完整解碼'
                    : checkBlanks ? '掃描並檢查中斷'
                    : includeMp4 ? '掃描所有影片' : '掃描非 MP4 檔案' }}</button>
        </div>
        <div v-if="help === 'scan'" class="tip">
          會列出所選資料夾內所有非 MP4 的影片，並判斷每個檔案能否無損換殼。
          <br><b>沿用上次的結果</b>：每一種檢查都會記住每支影片的結果，檔案大小與修改時間
          都沒變就不再重查，直接沿用（有問題的照樣列出來）。重新下載覆蓋過的檔案時間會變，
          所以會自動重查。勾「全部重新檢查」則這次全部重跑。
          <template v-if="checkDecode">
            <br><b>完整解碼檢查</b>：把每支影片（不分副檔名）的畫面從頭到尾解碼一次，
            找出「檔案讀得到、但畫面資料壞掉」的影片 —— 其他檢查只看檔案的外包裝，
            抓不到這種。聲音也一起解：聲音封包壞掉的影片在瀏覽器裡同樣會卡死，
            但畫面沒壞，可以修。實測一個 207 支影片的資料夾約 10 分鐘。檢查過且沒變動的檔案
            會記住結果，下次不再重跑；掃描中可以按「停止」，已檢查的照樣列出。
            <br>用 CPU 還是 GPU 解碼，跟著<b>設定 → 縮圖表 →「運算方式」</b>：CPU 每支會吃掉
            5～9 個核心；GPU 只佔不到半個，找出的損壞一樣（實測），但每支多佔
            150～480 MB 顯示記憶體。GPU 解不了的影片（例如 AV1）會自動改用 CPU。
          </template>
          <template v-if="checkBlanks">
            <br><b>檢查錄影中斷</b>：直播錄影常有這種情況 —— 主播離線或轉私人秀時，
            錄影程式沒有停、時間照走，卻收不到任何畫面與聲音，於是中間空了好幾分鐘，
            播放器停在同一格畫面。檔案本身沒有壞，所以完整解碼檢查抓不到。
            這個檢查只讀時間索引、不解碼（不分副檔名），找到的可以<b>無損去除空白</b>。
            畫面停住但聲音還在的不算空白，不會剪。
          </template>
          <template v-if="includeMp4">
            <br><b>也會檢查 .mp4</b>：副檔名只說明容器，說明不了裡面的編碼 ——
            例如 mpeg4（Xvid 時代）裝在 .mp4 裡，瀏覽器一樣播不動。這類檔案
            <b>重編碼後會直接取代原檔</b>（驗證通過才換），因為它得留在同一個檔名上。
          </template>
        </div>
      </div>

      <div v-if="scanning || running" class="prog">
        <Icon name="spinner" :size="13" class="spin" /> {{ scanning ? cs.progress : progress }}
        <button v-if="scanning && (checkDecode || checkBlanks)" class="lnk" :disabled="cs.stopping"
                @click="stopScan">{{ cs.stopping ? '停止中…' : '停止' }}</button>
        <!-- This used to promise the job continues in the background, copied
             from the duplicate-scan dialog where it is true — that one keeps
             its polling in dedup.js and its state in the store. The converter
             polls from inside this component, so closing loses the result
             even though the server keeps working. The close button is also
             disabled while converting, which made the old wording doubly
             wrong: it invited a click that does nothing.
             Since the scan job saves itself to the history on the server,
             closing no longer loses the result either: it finishes in the
             background, reopening the dialog while it runs picks it back up,
             and once done it opens from 歷史紀錄. -->
        <span class="bg">{{ running
          ? '轉檔進行中，無法關閉此視窗'
          : '可關閉此視窗，掃描會在背景繼續' }}</span>
      </div>
      <div v-if="running" class="bar"><div class="fill" :style="{ width: pct + '%' }"></div></div>
      <div v-if="error" class="err">{{ error }}</div>

      <!-- result summary -->
      <div v-if="result" class="done">
        <div class="ib"><Icon name="check" :size="15" />
          完成 {{ result.converted.length }} 個
          <template v-if="result.saved > 0">· 省下 {{ fmtSize(result.saved) }}</template>
        </div>
        <!-- how much a repair actually cost is only knowable afterwards, so
             it has to be reported here rather than promised up front -->
        <div v-for="c in (result.converted || []).filter(c => c.action === 'repair')"
             :key="c.dst" class="fixrow">
          {{ c.dst.split(/[\\/]/).pop() }} — 丟掉 {{ c.dropped }} / {{ c.fragments }} 段，
          約損失 {{ c.lost_seconds }} 秒，成品 {{ fmtDur(c.duration) }}；原始檔案已保留
        </div>
        <div v-for="c in (result.converted || []).filter(c => c.action === 'conceal')"
             :key="c.dst" class="fixrow">
          {{ c.dst.split(/[\\/]/).pop() }} — 已另存，原檔保留；
          到「壓縮」分頁的<b>待決定</b>清單比對後決定留哪個
        </div>
        <div v-for="f in result.failed" :key="f.path" class="failrow">
          {{ f.path.split(/[\\/]/).pop() }} — {{ f.error }}
        </div>
      </div>

      <!-- plan -->
      <!-- Hidden while the picker is open. Both panes scroll, and with no
           priority between them the browser splits the height by content —
           a long result list left the 745-row folder list a visible sliver.
           You are either setting up a run or reviewing the last one. -->
      <div v-if="items && !scanning && !scopeOpen && !showHistory" class="results">
        <div v-if="stopped" class="partial">已停止：只檢查了部分影片，下面只列出停止前找到的。</div>
        <div v-if="reusedText" class="reused">
          沿用上次的檢查結果（檔案沒有變動）：{{ reusedText }}。要全部重跑請勾「全部重新檢查」。
        </div>
        <div v-if="!items.length" class="empty ib">
          <Icon name="check" :size="15" />
          {{ checkDecode ? '所選範圍內沒有需要處理的影片，也沒有發現畫面損壞'
                         : '所選範圍內沒有非 MP4 的影片' }}</div>

        <template v-else>
          <div class="pickbar">
            <!-- Every group that can be picked has to be in the denominator.
                 It read "已選 1 / 0 個檔案" when only retimeable rows existed. -->
            已選 <b>{{ picked.length }}</b> / {{ remuxable.length + reencodable.length
              + repairable.length + retimeable.length + audiofixable.length
              + blankcuttable.length }} 個檔案
            <template v-if="pickedBytes">· {{ fmtSize(pickedBytes) }}</template>
            <span class="spacer"></span>
            <label v-if="anyReencode" class="crfrow">
              品質 CRF
              <input type="number" v-model.number="crf" min="14" max="35"
                     :disabled="running" style="width:52px" />
              <span class="mini">（越小越好，20 接近無損）</span>
              <label class="gpubox"><input type="checkbox" v-model="gpu" :disabled="running" /> 用 GPU</label>
            </label>
            <button class="btn" :disabled="running || !picked.length" @click="run">
              {{ running ? '轉檔中…' : '開始轉檔' }}</button>
          </div>

          <!-- remuxable -->
          <section v-if="remuxable.length">
            <div class="shead ok">
              <Icon name="check" :size="13" />
              可無損換殼（{{ remuxable.length }}）— 不重新編碼，畫質完全不變
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(remuxable, true)">全選</button>
              <button class="lnk" @click="setAll(remuxable, false)">全不選</button>
            </div>
            <label v-for="i in remuxable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk ok">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ i.width ? `${i.width}×${i.height}` : '?' }}</span>
              <span class="col">{{ fmtDur(i.duration) }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">{{ i.reason }}</span>
            </label>
          </section>

          <!-- needs re-encoding -->
          <section v-if="reencodable.length">
            <div class="shead warn">
              <Icon name="warn" :size="13" />
              必須重編碼（{{ reencodable.length }}）— 耗時且畫質有損，原檔一樣會刪除
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(reencodable, true)">全選</button>
              <button class="lnk" @click="setAll(reencodable, false)">全不選</button>
            </div>
            <label v-for="i in reencodable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk warn">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ i.width ? `${i.width}×${i.height}` : '?' }}</span>
              <span class="col">{{ fmtDur(i.duration) }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">{{ i.reason }}</span>
            </label>
          </section>

          <!-- damaged, but rescuable -->
          <section v-if="repairable.length">
            <div class="shead fix">
              <Icon name="wrench" :size="13" />
              可嘗試修復（{{ repairable.length }}）— 這些其實是 MP4，只是副檔名不符且有片段損壞
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(repairable, true)">全選</button>
              <button class="lnk" @click="setAll(repairable, false)">全不選</button>
              <button class="qmark" :class="{ on: help === 'repair' }"
                      title="說明" @click="toggleHelp('repair')">?</button>
            </div>
            <div v-if="help === 'repair'" class="note">
              保留的畫面逐位元組原樣複製，不重編碼。只丟掉讀不出來的片段 ——
              <b>事前無法知道會丟多少</b>，完成後才報告。
              <b>原始檔案不會被刪除</b>（跟上面兩種不同），要刪請用「刪除模式」。
              <br /><br />
              <b>為什麼要先「複製到本機」？</b>
              找壞片段是二分搜尋，要把檔案重複讀十幾次。影片碟是機械硬碟（實測約
              188 MB/s）、C 槽是固態硬碟，先複製一次再讀比較快 ——
              3.3 GB 的檔：直接讀約 106 秒，先複製約 25 秒（含複製 18 秒）。
              檔案在本機的 I 槽也一樣，不只 NAS。
            </div>
            <label v-for="i in repairable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk fix">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">{{ i.reason }}</span>
            </label>
          </section>

          <!-- plays, but the browser drops half the frames -->
          <section v-if="retimeable.length">
            <div class="shead ok">
              <Icon name="refresh" :size="13" />
              可修正影格時序（{{ retimeable.length }}）— 編碼沒問題，但瀏覽器會掉格
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(retimeable, true)">全選</button>
              <button class="lnk" @click="setAll(retimeable, false)">全不選</button>
              <button class="qmark" :class="{ on: help === 'retime' }"
                      title="說明" @click="toggleHelp('retime')">?</button>
            </div>
            <div v-if="help === 'retime'" class="note">
              這些檔案有 B 影格卻沒有顯示時間表，瀏覽器照解碼順序畫，
              <b>約一半的影格會被丟掉</b>（29.97fps 實際只剩約 15）。本機播放器
              自己從位元流重建順序，所以同一個檔在 VLC 很順、在瀏覽器很頓。
              <b>畫面逐格原樣複製、不重編碼</b>，音畫同步會保留，
              <b>驗證通過後直接取代原檔</b>。
            </div>
            <label v-for="i in retimeable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk ok">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">{{ i.reason }}</span>
            </label>
          </section>

          <!-- picture fine, audio packets corrupt -->
          <section v-if="audiofixable.length">
            <div class="shead ok">
              <Icon name="refresh" :size="13" />
              可修復（{{ audiofixable.length }}）— 瀏覽器播到或拖到這裡會卡死
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(audiofixable, true)">全選</button>
              <button class="lnk" @click="setAll(audiofixable, false)">全不選</button>
              <button class="qmark" :class="{ on: help === 'audiofix' }"
                      title="說明" @click="toggleHelp('audiofix')">?</button>
            </div>
            <div v-if="help === 'audiofix'" class="note">
              這些影片多半是有<b>斷訊的錄影</b>：斷訊的地方留下了殘缺的畫格或壞掉的聲音封包。
              VLC 這類播放器遇到會跳過，<b>瀏覽器遇到一個就整個播放器停擺</b>：
              播到那裡就停，拖進度條也完全沒反應。
              <br><b>修復斷訊</b>：丟掉每個斷訊前那一格殘缺的畫面（斷訊後都從完整的關鍵影格重新開始，
              所以不影響後面），其餘<b>原樣複製、不重新編碼</b>；聲音也壞了就順便修。
              修完會<b>整支完整解碼一次</b>，確定 0 個錯誤才換（需要幾分鐘）。
              <br><b>修復聲音</b>：影像原樣複製、畫質不變，只把聲音重新編碼（約 192 kbps），
              壞掉的片段補成靜音，音畫同步保留。
              <br>兩種都是<b>驗證通過才取代原檔</b>，沒通過就保留原檔。
            </div>
            <label v-for="i in audiofixable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk ok">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">{{ i.action === 'gapfix'
                ? `斷訊 ${i.gaps} 處、殘缺畫格 ${i.decode_errors} 行` +
                  (i.audio_errors ? `、壞聲音封包 ${i.audio_errors} 個` : '')
                : `壞掉的聲音封包 ${i.audio_errors} 個` }}</span>
              <button class="ib icon" title="播放" @click.prevent="viewing = i">
                <Icon name="play" :size="12" /></button>
            </label>
          </section>

          <!-- recordings with long empty stretches -->
          <section v-if="blankcuttable.length">
            <div class="shead ok">
              <Icon name="refresh" :size="13" />
              錄影中斷（{{ blankcuttable.length }}）— 中間有一大段沒有畫面也沒有聲音
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(blankcuttable, true)">全選</button>
              <button class="lnk" @click="setAll(blankcuttable, false)">全不選</button>
              <button class="qmark" :class="{ on: help === 'gapcut' }"
                      title="說明" @click="toggleHelp('gapcut')">?</button>
            </div>
            <div v-if="help === 'gapcut'" class="note">
              錄影程式在直播中斷時沒有停下來，時間照樣往前走，所以檔案裡留下了
              好幾分鐘<b>完全沒有畫面也沒有聲音</b>的空白，播放時停在同一格畫面。
              <br><b>去除空白</b>：把空白後面的內容往前接，每一格畫面與聲音都<b>原樣複製、不重新編碼</b>，
              畫質不變，只是片長變短。只去除 5 秒以上、而且空白後是完整關鍵影格的段落。
              <br>修完會比對<b>畫格數完全相同</b>、空白確實消失才換；.mp4 會<b>直接取代原檔</b>，
              其他格式會順便轉成 .mp4 並刪掉原檔。沒通過就保留原檔。
            </div>
            <label v-for="i in blankcuttable" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk ok">{{ label(i.action) }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">中斷 {{ i.blanks }} 處、空白 {{ fmtLen(i.removed) }}，
                去除後 {{ fmtLen(i.kept) }}</span>
              <button class="ib icon" title="播放" @click.prevent="viewing = i">
                <Icon name="play" :size="12" /></button>
            </label>
          </section>

          <!-- damaged picture data -->
          <section v-if="damaged.length">
            <div class="shead bad">
              <Icon name="warn" :size="13" />
              畫面損壞（{{ damaged.length }}）— 可重新編碼遮蔽損壞
              <span class="spacer"></span>
              <button class="lnk" @click="setAll(damaged, true)">全選</button>
              <button class="lnk" @click="setAll(damaged, false)">全不選</button>
              <button class="qmark" :class="{ on: help === 'damaged' }"
                      title="說明" @click="toggleHelp('damaged')">?</button>
            </div>
            <div v-if="help === 'damaged'" class="note">
              這些影片的<b>畫面資料本身壞了</b>，那一段的影像已經不在檔案裡，沒有辦法還原。
              瀏覽器播到壞掉的地方會停住，拖進度條跳進去也會卡住。
              <br><b>遮蔽損壞</b>：整支重新編碼（crf 23），壞掉的格子用附近的畫面補上。
              損壞那一兩秒<b>仍會看到花屏或停頓</b>，但新檔裡沒有壞資料了，
              瀏覽器可以正常播放與拖進度條；其餘畫面幾乎不變（實測相似度 0.98～0.995），
              大小與原檔差不多。720p 約 7 倍速。
              <br>會<b>另存成 xxx_fix.mp4、原檔保留</b>，修完放進「壓縮」分頁的<b>待決定</b>清單，
              那裡標出損壞處，點一下左右兩邊一起跳過去比對。
              <br>損壞太嚴重的（例如好幾分鐘一格都解不出來）修完會變短，驗證會擋下來、不產生新檔。
              也可以按 ▶ 確認壞在哪裡，用<b>剪輯</b>剪出完好的部分，或在<b>刪除模式</b>刪掉。
              分級看解碼錯誤的行數：1,000 行以下輕微、1,000～10,000 中等、超過 10,000 嚴重。
            </div>
            <label v-for="i in damaged" :key="i.path" class="row">
              <input type="checkbox" :checked="chosen.has(i.path)"
                     :disabled="running" @change="toggle(i)" />
              <span class="tagk" :class="'lv' + i.damage">{{ i.damage }}</span>
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="col">{{ fmtDur(i.duration) }}</span>
              <span class="col">{{ fmtSize(i.size) }}</span>
              <span class="why">解碼錯誤 {{ i.decode_errors.toLocaleString() }} 行<template
                v-if="i.regions && i.regions.length"> · 損壞處 {{ i.regions.slice(0, 3)
                  .map(span).join('、') }}<template
                  v-if="i.regions.length > 3"> 等 {{ i.regions.length }} 處</template></template></span>
              <button class="ib icon" title="播放" @click.prevent="viewing = i">
                <Icon name="play" :size="12" /></button>
            </label>
          </section>

          <!-- skipped -->
          <section v-if="skipped.length">
            <div class="shead dim">
              <Icon name="skip" :size="13" /> 略過（{{ skipped.length }}）
            </div>
            <div v-for="i in skipped" :key="i.path" class="row dim">
              <span class="fn" :title="i.path">{{ i.name }}</span>
              <span class="why">{{ i.reason }}</span>
            </div>
          </section>
        </template>
      </div>
      </div>
    <div v-if="viewing" class="pmask" @click.self="viewing = null">
      <div class="pbox">
        <VideoPlayer ref="player" :key="viewing.path" :src="streamUrl(viewing.path)"
                     @close="viewing = null" />
        <div v-if="viewing.decode_errors || viewing.audio_errors" class="regbar">
          <template v-if="locateErr">無法定位損壞處：{{ locateErr }}</template>
          <template v-else-if="regions === null">
            <Icon name="spinner" :size="12" class="spin" /> 正在找損壞處（這支是較早檢查的，沒有記下位置，要再解碼一次）…
          </template>
          <template v-else-if="!regions.length">找不到損壞處的位置</template>
          <template v-else>
            損壞處（點一下跳到前 {{ LEAD_IN }} 秒）：
            <button v-for="(g, gi) in regions.slice(0, 20)" :key="gi" class="chipbtn"
                    :title="`${span(g)}，${g.errors} 行錯誤`"
                    @click="jump(g)">{{ span(g) }}</button>
            <template v-if="regions.length > 20">…還有 {{ regions.length - 20 }} 處</template>
            <span class="regnote">瀏覽器播到損壞處可能會停住，那正是這支影片的問題。</span>
          </template>
        </div>
      </div>
    </div>
  </SideDialog>
</template>

<style scoped>
/* scoped styles do not cross components, so these cannot be borrowed from
   SettingsDialog even though they look the same */

.tabbody { display: flex; flex-direction: column; flex: 1; min-height: 0;
  overflow-y: auto; }

.barrow { display: flex; align-items: stretch; }
.barrow .scopebar { flex: 1; }
.histbtn { background: none; border: none; border-bottom: 1px solid #313148;
  border-left: 1px solid #313148; color: var(--dim); font-size: 12px;
  padding: 0 14px; cursor: pointer; white-space: nowrap; gap: 4px; }
.histbtn:hover:not(:disabled) { color: var(--accent); }
.histpane { flex: 1; min-height: 0; overflow-y: auto; }
.lastscan { gap: 6px; margin-bottom: 8px; padding: 6px 10px; font-size: 12px;
  color: var(--dim); background: #1b1b33; border-radius: 6px; }
.lastscan .spacer { flex: 1; }
.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 16px; background: none; border: none;
  border-bottom: 1px solid #313148; color: var(--dim); font-size: 12px;
  cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
/* the options area is a card, like the settings dialog's; the result lists
   below it stay as they were */
.scopewrap { margin: 4px 16px 8px; padding: 12px 14px 2px; background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; }
.chk { display: flex; align-items: center; gap: 6px; color: var(--dim);
  font-size: 11px; cursor: pointer; }
.chk input { margin: 0; }
.opts { display: flex; align-items: center; gap: 12px; padding: 4px 0 10px; }
.chks { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.chk.sub { padding-left: 20px; }
.scanbtn { flex-shrink: 0; white-space: nowrap; }
.spacer { flex: 1; }
.tip { color: var(--dim); font-size: 11px; padding: 0 0 12px; }

.prog { padding: 12px 16px; color: var(--dim); font-size: 12px;
  display: flex; align-items: center; gap: 8px; }
.prog .bg { margin-left: auto; color: #f9e2af; }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.bar { height: 4px; background: #26263a; margin: 0 16px 10px; border-radius: 2px; }
.fill { height: 100%; background: var(--accent); border-radius: 2px;
  transition: width .2s; }
.err { padding: 12px 16px; color: var(--danger); font-size: 13px; }
.empty { color: var(--success); padding: 24px; justify-content: center; }
.done { padding: 10px 16px; color: var(--success); font-size: 12px;
  border-bottom: 1px solid #313148; }
.failrow { color: var(--danger); font-size: 11px; margin-top: 4px; }

.pickbar { display: flex; align-items: center; gap: 10px; padding: 10px 0;
  font-size: 12px; position: sticky; top: 0; background: var(--panel); z-index: 2; }
.pickbar b { color: var(--accent); }
.crfrow { display: flex; align-items: center; gap: 6px; font-size: 11px;
  color: var(--dim); }
.mini { font-size: 10px; }
.gpubox { display: flex; align-items: center; gap: 4px; }

/* no overflow of its own — .tabbody scrolls */
.results { padding: 0 16px 16px; }
.results section { margin-bottom: 14px; }
.shead { display: flex; align-items: center; gap: 6px; font-size: 11px;
  padding: 5px 0; border-bottom: 1px solid #313148; margin-bottom: 4px; }
.shead.ok { color: var(--success); }
.shead.warn { color: #f9e2af; }
.shead.dim { color: var(--dim); }
.shead.fix { color: var(--accent); }
.note { font-size: 11px; color: var(--dim); line-height: 1.6;
  background: var(--card); border-radius: 5px; padding: 6px 9px;
  margin-bottom: 6px; }
.note b { color: var(--text); }
.fixrow { font-size: 11px; color: var(--accent); margin-top: 4px; }
.lnk { background: none; border: none; color: var(--dim); font-size: 11px;
  cursor: pointer; padding: 0 4px; }
.lnk:hover { color: var(--accent); text-decoration: underline; }
/* Explanations are collapsed behind these. They are read once and then never
   again, while the rows underneath are what the dialog is actually for —
   three permanently-open paragraphs pushed the file list off the screen. */
.qmark {
  background: none; border: 1px solid var(--line, #3a3a52); color: var(--dim);
  border-radius: 50%; width: 17px; height: 17px; line-height: 1;
  font-size: 11px; cursor: pointer; padding: 0; flex-shrink: 0;
}
.qmark:hover { color: var(--accent); border-color: var(--accent); }
.qmark.on { color: var(--accent); border-color: var(--accent); }

.row { display: flex; align-items: center; gap: 8px; padding: 3px 6px;
  border-radius: 3px; font-size: 11px; cursor: pointer; }
.row:hover { background: #26263a; }
.row.dim { color: var(--dim); cursor: default; }
.tagk { font-size: 10px; border-radius: 3px; padding: 1px 7px; flex: 0 0 auto; }
.tagk.ok { background: #162416; color: var(--success); }
.tagk.warn { background: #2a2010; color: #f9e2af; }
.tagk.fix { background: #1b1b33; color: var(--accent); }
.tagk.lv輕微 { background: #2a2010; color: #f9e2af; }
.tagk.lv中等 { background: #3a2410; color: #fab387; }
.tagk.lv嚴重 { background: #5a2020; color: var(--danger); }
.shead.bad { color: var(--danger); }
.partial { margin: 0 0 8px; padding: 6px 10px; font-size: 12px; color: #f9e2af;
  background: #2a2010; border-radius: 6px; }
.reused { margin: 0 0 8px; padding: 6px 10px; font-size: 12px; color: var(--muted, #9aa);
  background: var(--panel2, #1c1f26); border-radius: 6px; }
.row .icon { width: 24px; height: 20px; border: none; border-radius: 3px;
  background: #1a2a3a; color: #7eb8f7; justify-content: center; cursor: pointer;
  flex: 0 0 auto; }
.pmask { position: fixed; inset: 0; z-index: 90; background: rgba(0,0,0,.85);
  display: flex; align-items: center; justify-content: center; }
.pbox { width: min(1100px, 92vw); }
/* the damaged spots, under the player */
.regbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  margin-top: 8px; padding: 8px 10px; background: var(--card); border-radius: 6px;
  font-size: 12px; color: var(--dim); }
.chipbtn { background: transparent; border: 1px solid #555; border-radius: 999px;
  color: var(--text); font-size: 11px; padding: 1px 8px; cursor: pointer; }
.chipbtn:hover { border-color: var(--accent); color: var(--accent); }
.regnote { flex-basis: 100%; color: var(--dim); font-size: 11px; }
.fn { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.col { color: var(--dim); font-size: 10px; flex: 0 0 74px; }
.why { color: var(--dim); font-size: 10px; flex: 0 0 250px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
