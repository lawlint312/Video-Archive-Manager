<script setup>
/**
 * Face index: choose characters, run now or on a schedule, see what ran.
 *
 * It used to be one button that indexed every character with no way to stop
 * it — about 4 s a video and ~4,000 videos left, so nobody dared press it.
 * Now the folder picker shows what each character still needs (and the time
 * that adds up to), a run can be stopped and continues where it stopped, and
 * a schedule runs it at night with an end time (core/schedule.py has the
 * rules: catching up happens only inside the window).
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api, errText } from '../api'
import { setStatus, setBusy } from '../store'
import { fmtEta, fmtCount } from '../scanprogress.js'
import ScopePicker from './ScopePicker.vue'
import SideDialog from './SideDialog.vue'
import Icon from './Icon.vue'
import { t, N_ } from '../i18n'
import { ts } from '../serverstrings.js'

const emit = defineEmits(['close'])

const TABS = computed(() => [
  { key: 'run', icon: 'face', label: t('立即執行'), heading: t('人臉索引') },
  { key: 'schedule', icon: 'calendar', label: t('排程'),
    heading: t('排程（指定時間自動執行）') },
  { key: 'history', icon: 'history', label: t('執行紀錄') },
])
const tab = ref('run')

// ── folders and what they still need ──────────────────────────────────────
const info = ref(null)          // /api/face/folders
const error = ref('')
const selected = ref([])
const picker = ref(null)

async function load() {
  try { info.value = await api('/api/face/folders') }
  catch (e) { error.value = t('無法載入：') + errText(e) }
}

const secPer = computed(() => info.value?.sec_per_video || 4)
const rowInfo = computed(() => {
  const out = {}
  for (const [p, c] of Object.entries(info.value?.folders || {})) {
    out[p] = c.pending
      ? { text: t('未索引 {n}', { n: c.pending }),
          title: t('影片 {v} 部，已處理 {d} 部', { v: c.videos, d: c.indexed }) }
      : { text: c.videos ? t('完成') : t('沒有影片'), dim: true }
  }
  return out
})
// How long a piece of work will take, before it starts. fmtEta is a countdown
// for something already running — "快好了" there means "nearly finished",
// which read wrong next to "2 left, not started" (2026-11-07)
function fmtWork(sec) {
  if (!sec) return ''
  return sec < 60 ? t('不到 1 分鐘') : fmtEta(sec)
}
const pendingOf = paths => paths.reduce((n, p) => n + (info.value?.folders?.[p]?.pending || 0), 0)
const selPending = computed(() => pendingOf(selected.value))
const allPending = computed(() =>
  Object.values(info.value?.folders || {}).reduce((n, c) => n + c.pending, 0))
const totalVideos = computed(() =>
  Object.values(info.value?.folders || {}).reduce((n, c) => n + c.videos, 0))
const donePct = computed(() => totalVideos.value
  ? Math.round((totalVideos.value - allPending.value) / totalVideos.value * 100) : 0)
// why the start button is off, said next to it — a disabled button that looks
// like any other was all the user got ("按了沒反應", 2026-11-07)
const blockReason = computed(() => {
  if (!info.value) return t('準備中…')
  if (!info.value.available) return ts(info.value.reason) || t('人臉辨識不可用')
  if (info.value.busy) return t('另一個人臉索引正在執行中')
  if (!selected.value.length) return t('請先勾選角色')
  if (!selPending.value) return t('選的角色都已索引完成')
  return ''
})
const pickUnfinished = () =>
  picker.value?.selectWhere(p => (info.value?.folders?.[p]?.pending || 0) > 0)

// ── running ────────────────────────────────────────────────────────────────
const jobId = ref(null)
const prog = ref(null)
const stopping = ref(false)
const starting = ref(false)     // the request is out: a second click would 409
const lastResult = ref(null)
let timer = null

const running = computed(() => !!jobId.value)
const pct = computed(() => prog.value?.total
  ? Math.round(prog.value.done / prog.value.total * 100) : 0)
const eta = computed(() => {
  const p = prog.value
  if (!p?.total) return ''
  const sec = p.sec || secPer.value
  return fmtEta((p.total - p.done) * sec)
})

async function start() {
  if (!selected.value.length || starting.value) return
  starting.value = true
  error.value = ''
  lastResult.value = null
  try {
    const r = await api('/api/face/index', {
      method: 'POST', body: JSON.stringify({ paths: selected.value }) })
    follow(r.job_id)
  } catch (e) { error.value = t('無法開始：') + errText(e) }
  finally { starting.value = false }
}

function follow(id) {
  jobId.value = id
  stopping.value = false
  clearInterval(timer)
  timer = setInterval(poll, 1000)
  poll()
}

async function poll() {
  if (!jobId.value) return
  let j
  try { j = await api(`/api/face/job/${jobId.value}`) } catch { return }
  if (j.status === 'running') {
    if (j.progress && typeof j.progress === 'object') {
      prog.value = j.progress
      setBusy(t('人臉索引：{person} / {file}', j.progress))
    }
    return
  }
  clearInterval(timer)
  jobId.value = null
  prog.value = null
  setBusy('')
  if (j.status === 'error') {
    error.value = t('索引失敗：') + ts(j.error)
  } else {
    error.value = ''
    lastResult.value = j.result
    setStatus(t('人臉索引') + ' — ' + resultText(j.result), j.result?.stopped ? 'warn' : 'ok')
  }
  load()
}

async function stop() {
  stopping.value = true
  try { await api(`/api/jobs/${jobId.value}/cancel`, { method: 'POST' }) }
  catch (e) { error.value = errText(e); stopping.value = false }
}

// ── schedules ──────────────────────────────────────────────────────────────
const schedules = ref([])
const serverNow = ref('')
const WEEK = [N_('週一'), N_('週二'), N_('週三'), N_('週四'), N_('週五'), N_('週六'), N_('週日')]
const STATE = {
  off: N_('已停用'), due: N_('等待開始'), started: N_('本時段已執行'),
  running: N_('執行中'), scheduled: N_('已排定'), done: N_('已完成'), missed: N_('已錯過'),
}

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const form = ref({ repeat: 'daily', date: today(), weekdays: [5, 6],
                   start: '01:00', hasEnd: true, end: '07:00', scope: 'all' })
const saving = ref(false)

async function loadSchedules() {
  try {
    const r = await api('/api/face/schedules')
    schedules.value = r.schedules
    serverNow.value = r.now.replace('T', ' ')
  } catch (e) { error.value = t('無法載入排程：') + errText(e) }
}

function toggleDay(d) {
  const w = new Set(form.value.weekdays)
  w.has(d) ? w.delete(d) : w.add(d)
  form.value.weekdays = [...w].sort()
}

// how many videos one window can get through, at the measured pace
const perWindow = computed(() => {
  const f = form.value
  if (!f.hasEnd || !f.start || !f.end || f.start === f.end) return null
  const [sh, sm] = f.start.split(':').map(Number)
  const [eh, em] = f.end.split(':').map(Number)
  let min = (eh * 60 + em) - (sh * 60 + sm)
  if (min <= 0) min += 24 * 60
  return { hours: Math.round(min / 6) / 10, videos: Math.floor(min * 60 / secPer.value) }
})
const formPending = computed(() =>
  form.value.scope === 'all' ? allPending.value : selPending.value)

async function addSchedule() {
  const f = form.value
  if (f.scope === 'picked' && !selected.value.length) {
    error.value = t('請先在「立即執行」勾選角色，或選擇全部角色'); return
  }
  saving.value = true
  error.value = ''
  try {
    await api('/api/face/schedules', { method: 'POST', body: JSON.stringify({
      repeat: f.repeat, date: f.repeat === 'once' ? f.date : null,
      weekdays: f.repeat === 'weekly' ? f.weekdays : [],
      start: f.start, end: f.hasEnd ? f.end : null,
      all: f.scope === 'all', paths: f.scope === 'all' ? [] : selected.value,
    }) })
    await loadSchedules()
    setStatus(t('已新增排程'), 'ok')
  } catch (e) { error.value = t('無法新增排程：') + errText(e) }
  finally { saving.value = false }
}

async function setEnabled(s, on) {
  try {
    await api(`/api/face/schedules/${s.id}`, { method: 'PATCH',
                                               body: JSON.stringify({ enabled: on }) })
    await loadSchedules()
  } catch (e) { error.value = errText(e) }
}

async function remove(s) {
  if (!confirm(t('刪除這個排程？'))) return
  try {
    await api(`/api/face/schedules/${s.id}`, { method: 'DELETE' })
    await loadSchedules()
  } catch (e) { error.value = errText(e) }
}

function when(s) {
  const win = s.end ? `${s.start}–${s.end}` : t('{t} 起', { t: s.start })
  if (s.repeat === 'once') return `${s.date} ${win}`
  if (s.repeat === 'daily') return t('每天 {win}', { win })
  return t('每{days} {win}', { days: s.weekdays.map(d => t(WEEK[d])).join(t('、')), win })
}
function scopeText(s) {
  if (s.all) return t('全部角色')
  const n = s.names.length
  return n <= 3 ? s.names.join(t('、'))
    : t('{names} 等 {n} 個角色', { names: s.names.slice(0, 3).join(t('、')), n })
}

// ── results (shared by the history tab and the schedules) ──────────────────
function fmtWhen(epoch) {
  const d = new Date(epoch * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function fmtSec(s) {
  if (s < 60) return t('{n} 秒', { n: Math.round(s || 0) })
  const m = Math.round(s / 60)
  return m < 60 ? t('{n} 分鐘', { n: m })
    : t('{h} 小時 {m} 分', { h: Math.floor(m / 60), m: m % 60 })
}
function resultText(r) {
  if (!r) return ''
  if (r.error) return t('失敗：{e}', { e: ts(r.error) })
  let s = t('處理 {n} 部（找到臉 {k} 部），{time}',
            { n: fmtCount(r.processed || 0), k: fmtCount(r.indexed || 0), time: fmtSec(r.seconds) })
  if (r.stopped === 'cancelled') s += t('，手動停止')
  else if (r.stopped === 'deadline') s += t('，到結束時間停止')
  const left = (r.total || 0) - (r.processed || 0)
  if (left > 0) s += t('，還剩 {n} 部', { n: fmtCount(left) })
  if (r.missing) s += t('，{n} 個資料夾找不到（已改名、移除或暫時無法存取）', { n: r.missing })
  return s
}

onMounted(async () => {
  await load()
  if (info.value?.active) follow(info.value.active)
  loadSchedules()
})
onBeforeUnmount(() => { clearInterval(timer); if (running.value) setBusy('') })
</script>

<template>
  <SideDialog :title="t('人臉索引')" :items="TABS" v-model="tab"
              width="1100px" height="900px" pad="24px" @close="emit('close')">
    <!-- a scroller and, on the run page, an action bar pinned under it: the
         button has to stay in reach however long the folder list is -->
    <div class="page">
      <div class="scroll">
        <div v-if="info && !info.available" class="warn ib">
          <Icon name="warn" :size="13" /> {{ ts(info.reason) || t('人臉辨識不可用') }}
        </div>
        <div v-if="error" class="err">{{ error }}</div>

        <!-- run now: v-show, so the picker (and what is ticked) survives a tab switch -->
        <div v-show="tab === 'run'" class="stack runpage">
          <div class="stats">
            <div class="stat">
              <div class="num warnc">{{ info ? fmtCount(allPending) : '…' }}</div>
              <div class="cap">{{ t('未索引的影片') }}</div>
            </div>
            <div class="stat">
              <div class="num">{{ info ? fmtCount(totalVideos - allPending) : '…' }}<span
                  class="of"> / {{ fmtCount(totalVideos) }}</span></div>
              <div class="cap">{{ t('已處理的影片') }}</div>
              <div class="mini"><div class="fill" :style="{ width: donePct + '%' }"></div></div>
            </div>
            <div class="stat">
              <div class="num">{{ !info ? '…' : allPending ? fmtWork(allPending * secPer) : t('完成') }}</div>
              <div class="cap">{{ t('全部做完（每部約 {n} 秒）', { n: secPer }) }}</div>
            </div>
          </div>

          <div class="card pick">
            <div class="cardhead">
              <span>{{ t('選擇角色') }}</span>
              <span class="spacer"></span>
              <button class="lnk" :disabled="running || !info || !allPending" @click="pickUnfinished">
                {{ t('勾選所有未完成的') }}</button>
            </div>
            <ScopePicker ref="picker" storage-key="vam.face.picked" :default-all="false"
                         :remember="false" :show-inbox="false" :info="rowInfo"
                         :disabled="running" @update:selected="v => selected = v" />
          </div>

          <div v-if="lastResult" class="done ib">
            <Icon name="check" :size="14" /> {{ resultText(lastResult) }}
          </div>
        </div>

        <!-- schedules -->
        <div v-show="tab === 'schedule'" class="stack">
          <div class="card">
            <div class="cardhead"><span>{{ t('新增排程') }}</span></div>
            <div class="grid">
              <span class="lab">{{ t('重複') }}</span>
              <div class="ctl">
                <label class="radio"><input type="radio" value="once" v-model="form.repeat" /> {{ t('單次') }}</label>
                <label class="radio"><input type="radio" value="daily" v-model="form.repeat" /> {{ t('每天') }}</label>
                <label class="radio"><input type="radio" value="weekly" v-model="form.repeat" /> {{ t('每週') }}</label>
              </div>

              <template v-if="form.repeat === 'once'">
                <span class="lab">{{ t('日期') }}</span>
                <div class="ctl"><input type="date" v-model="form.date" class="inp" /></div>
              </template>
              <template v-if="form.repeat === 'weekly'">
                <span class="lab">{{ t('星期') }}</span>
                <div class="ctl">
                  <button v-for="(w, i) in WEEK" :key="i" class="chip"
                          :class="{ on: form.weekdays.includes(i) }" @click="toggleDay(i)">{{ t(w) }}</button>
                </div>
              </template>

              <span class="lab">{{ t('時間') }}</span>
              <div class="ctl">
                <input type="time" v-model="form.start" class="inp" />
                <label class="chk"><input type="checkbox" v-model="form.hasEnd" /> {{ t('到這個時間停止') }}</label>
                <input type="time" v-model="form.end" class="inp" :disabled="!form.hasEnd" />
              </div>
              <span></span>
              <div class="ctl note">
                <template v-if="form.hasEnd">{{ t('沒做完的，下一次排程接著做。結束時間比開始早，表示隔天早上。') }}</template>
                <template v-else>{{ t('一直做到全部完成，或手動停止。') }}</template>
                {{ t('到了開始時間，如果電腦沒開機或服務沒在執行：開機時還在時間範圍內就會補跑，否則等下一次。') }}
              </div>

              <span class="lab">{{ t('範圍') }}</span>
              <div class="ctl col">
                <label class="radio"><input type="radio" value="all" v-model="form.scope" />
                  {{ t('全部角色（含之後新增的）') }}</label>
                <label class="radio"><input type="radio" value="picked" v-model="form.scope" />
                  {{ t('「立即執行」勾選的 {n} 個資料夾', { n: selected.length }) }}</label>
              </div>
              <span></span>
              <div class="ctl note">
                <template v-if="formPending">{{ t('目前未索引 {n} 部（{time}）', { n: fmtCount(formPending), time: fmtWork(formPending * secPer) }) }}</template><template
                  v-else>{{ t('目前沒有未索引的影片，排程會處理之後新增的') }}</template><template
                  v-if="perWindow">{{ t('；每次 {h} 小時約可處理 {v} 部', { h: perWindow.hours, v: fmtCount(perWindow.videos) }) }}</template>
              </div>
            </div>
            <div class="cardfoot">
              <button class="btn" :disabled="saving" @click="addSchedule">
                <Icon name="plus" :size="14" /> {{ t('新增排程') }}</button>
              <span class="dim small">{{ t('時間以伺服器為準（現在 {now}）', { now: serverNow }) }}</span>
            </div>
          </div>

          <div class="sectitle">{{ t('排程') }} <span class="count">{{ schedules.length }}</span></div>
          <div v-if="!schedules.length" class="dim empty">{{ t('還沒有排程') }}</div>
          <div v-for="s in schedules" :key="s.id" class="sched" :class="{ off: !s.enabled }">
            <div class="srow">
              <Icon name="calendar" :size="15" class="sicon" />
              <b>{{ when(s) }}</b>
              <span class="badge" :class="s.state">{{ t(STATE[s.state] || s.state) }}</span>
              <span class="spacer"></span>
              <button class="lnk" @click="setEnabled(s, !s.enabled)">
                {{ s.enabled ? t('停用') : t('啟用') }}</button>
              <button class="lnk danger" @click="remove(s)">{{ t('刪除') }}</button>
            </div>
            <div class="sdetail">
              <span>{{ scopeText(s) }}<template v-if="s.owner && s.owner !== '?'"> · {{ s.owner }}</template></span>
              <span v-if="s.next">{{ t('下一次：{t}', { t: s.next.replace('T', ' ') }) }}</span>
              <span v-if="s.last_result">{{ t('上一次（{t}）：', { t: fmtWhen(s.last_result.at) }) }}{{ resultText(s.last_result) }}</span>
            </div>
          </div>
        </div>

        <!-- history -->
        <div v-show="tab === 'history'" class="stack">
          <div v-if="!info?.runs?.length" class="dim empty">{{ t('還沒有執行紀錄') }}</div>
          <div v-else class="card flush">
            <div v-for="(r, i) in info.runs" :key="i" class="hrow">
              <span class="when">{{ fmtWhen(r.at) }}</span>
              <span class="badge" :class="r.trigger">{{ r.trigger === 'schedule' ? t('排程') : t('手動') }}</span>
              <span class="txt" :class="{ bad: r.error }">{{ resultText(r) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="tab === 'run'" class="actionbar">
        <template v-if="running">
          <div class="progwrap">
            <div class="line">
              <Icon name="spinner" :size="13" class="spin" />
              <template v-if="prog">
                <b>{{ fmtCount(prog.done) }} / {{ fmtCount(prog.total) }}</b>
                <span class="cur">{{ prog.person }} / {{ prog.file }}</span>
                <span class="eta">{{ eta }}</span>
              </template>
              <template v-else>{{ t('準備中…') }}</template>
            </div>
            <div class="bar"><div class="fill" :style="{ width: pct + '%' }"></div></div>
            <div class="dim small">{{ t('可以關閉這個視窗，索引會在背景繼續') }} ·
              {{ t('停止後，做完的會保留，下次從還沒做的開始') }}</div>
          </div>
          <button class="btn ghost" :disabled="stopping" @click="stop">
            <Icon name="stop" :size="14" /> {{ stopping ? t('停止中…') : t('停止') }}
          </button>
        </template>
        <template v-else>
          <div class="summary">
            <template v-if="selected.length">
              {{ t('已選 {n} 個資料夾，未索引 {k} 部', { n: selected.length, k: fmtCount(selPending) }) }}<template
                v-if="selPending"> · {{ fmtWork(selPending * secPer) }}</template>
            </template>
            <div v-if="blockReason" class="why">{{ blockReason }}</div>
          </div>
          <button class="btn" :disabled="!!blockReason || starting" @click="start">
            <Icon name="face" :size="14" /> {{ t('開始索引') }}
          </button>
        </template>
      </div>
    </div>
  </SideDialog>
</template>

<style scoped>
.page { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 4px 24px 20px;
  display: flex; flex-direction: column; gap: 12px; }
.stack { display: flex; flex-direction: column; gap: 14px; }
.runpage { flex: 1; min-height: 0; }
.runpage .card.pick { flex: 1; min-height: 260px; display: flex; flex-direction: column; }
.runpage .card.pick :deep(.scope) { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.runpage .card.pick :deep(.scope section:last-child) { flex: 1; min-height: 0;
  display: flex; flex-direction: column; margin-bottom: 0; }
.runpage .card.pick :deep(.charbox) { flex: 1; max-height: none; }
.spacer { flex: 1; }
.dim { color: var(--dim); font-size: 12px; }
.small { font-size: 11px; }
.btn:disabled { opacity: .45; cursor: default; }
.btn:disabled:hover { background: var(--accent); }
.btn.ghost:disabled:hover { background: var(--card); }
.lnk { background: none; border: none; color: var(--accent); font-size: 12px;
  cursor: pointer; padding: 0; }
.lnk:disabled { color: var(--dim); cursor: default; }
.lnk.danger { color: var(--danger); }
.warn { gap: 6px; color: #f9e2af; font-size: 12px; background: #2a2a1b;
  padding: 8px 12px; border-radius: 8px; }
.err { color: var(--danger); font-size: 12px; background: #2e1b22;
  padding: 8px 12px; border-radius: 8px; }

/* the three numbers that answer "how much is left, and how long is that" */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.stat { background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px;
  padding: 12px 16px; }
.num { font-size: 22px; font-weight: 600; color: var(--text); line-height: 1.3; }
.num.warnc { color: #f9e2af; }
.num .of { font-size: 13px; font-weight: 400; color: var(--dim); }
.cap { font-size: 12px; color: var(--dim); margin-top: 2px; }
.mini { height: 4px; background: #313148; border-radius: 2px; margin-top: 8px; overflow: hidden; }
.mini .fill { height: 100%; background: #a6e3a1; }

.card { background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px;
  padding: 14px 16px; }
.card.flush { padding: 4px 16px; }
.cardhead { display: flex; align-items: center; gap: 8px; font-size: 13px;
  font-weight: 600; color: var(--text); margin-bottom: 12px; }
.cardfoot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 16px; padding-top: 14px; border-top: 1px solid #ffffff0d; }
.done { gap: 6px; color: #a6e3a1; font-size: 12px; background: #1b2a22;
  padding: 8px 12px; border-radius: 8px; }

/* the run page's action bar */
.actionbar { flex: 0 0 auto; display: flex; align-items: center; gap: 16px;
  padding: 12px 24px; border-top: 1px solid #313148; background: var(--panel); }
.summary { flex: 1; min-width: 0; font-size: 12px; color: var(--text); }
.summary .why { color: var(--dim); font-size: 11px; margin-top: 2px; }
.progwrap { flex: 1; min-width: 0; }
.progwrap .line { display: flex; align-items: center; gap: 8px; font-size: 12px; min-width: 0; }
.progwrap .cur { flex: 1; min-width: 0; color: var(--dim); overflow: hidden;
  white-space: nowrap; text-overflow: ellipsis; }
.progwrap .eta { color: var(--dim); white-space: nowrap; }
.bar { height: 6px; background: #313148; border-radius: 3px; margin: 6px 0; overflow: hidden; }
.bar .fill { height: 100%; background: var(--accent); transition: width .4s; }
.spin { animation: sp 1s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }

/* the schedule form: labels in one column, controls in the other */
.grid { display: grid; grid-template-columns: max-content 1fr; gap: 12px 16px;
  align-items: center; font-size: 12px; }
.lab { color: var(--dim); }
.ctl { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; min-width: 0; }
.ctl.col { flex-direction: column; align-items: flex-start; gap: 6px; }
.ctl.note { color: var(--dim); font-size: 11px; line-height: 1.6; margin-top: -6px; }
.radio, .chk { display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
  color: var(--text); }
.inp { background: #1b1b2a; border: 1px solid #313148; color: var(--text);
  border-radius: 6px; padding: 5px 8px; font-size: 12px; color-scheme: dark; }
.inp:disabled { opacity: .4; }
.chip { background: transparent; border: 1px solid #313148; color: var(--dim);
  border-radius: 999px; padding: 3px 11px; font-size: 12px; cursor: pointer; }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }

.sectitle { font-size: 13px; font-weight: 600; color: var(--text); margin-top: 4px; }
.sectitle .count { color: var(--dim); font-weight: 400; margin-left: 4px; }
.empty { padding: 4px 2px; }
.sched { background: var(--card); border: 1px solid #ffffff0d; border-radius: 10px;
  padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
.sched.off { opacity: .55; }
.srow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; }
.sicon { color: var(--dim); }
.sdetail { display: flex; flex-direction: column; gap: 2px; padding-left: 25px;
  font-size: 11px; color: var(--dim); }
.badge { font-size: 11px; border-radius: 4px; padding: 0 6px; border: 1px solid #313148;
  color: var(--dim); white-space: nowrap; }
.badge.running, .badge.due { color: #a6e3a1; border-color: #a6e3a1; }
.badge.scheduled { color: var(--accent); border-color: var(--accent); }
.badge.missed { color: #f9e2af; border-color: #f9e2af; }
.badge.schedule { color: var(--accent); border-color: var(--accent); }
.hrow { display: flex; align-items: baseline; gap: 12px; font-size: 12px;
  padding: 9px 0; border-bottom: 1px solid #ffffff0d; }
.hrow:last-child { border-bottom: none; }
.hrow .when { color: var(--dim); flex: 0 0 auto; min-width: 80px; }
.hrow .txt { flex: 1; }
.hrow .txt.bad { color: var(--danger); }
</style>
