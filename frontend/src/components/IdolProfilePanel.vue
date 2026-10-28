<script setup>
/**
 * Fill character profiles from javdatabase.com.
 *
 * Strictly two phases: scan produces a preview and writes nothing, then the
 * user approves and only then does anything reach person_info.json. Those
 * files live on the NAS where git cannot help, and an earlier version of this
 * idea (/api/javdb/seed) wrote 28 wrong aliases before anyone saw them.
 */
import { ref, computed } from 'vue'
import { api, errText, imageThumbUrl } from '../api'
import { store, setStatus, setBusy } from '../store'
import { formatDob, formatAge, FIELD_LABELS } from '../profilefields'
import CharPicker from './CharPicker.vue'
import Icon from './Icon.vue'

const emit = defineEmits(['close'])

// JavDialog disables its close button while this is true: closing on
// a scan or a write in progress abandons a job with no way back to it.
const busy = computed(() => scanning.value || applying.value)
defineExpose({ busy })

const selected = ref([])
// 「也查詢已有的個資與大頭照」: off, only what is empty gets filled
const recheck = ref(false)
const scanning = ref(false)
const applying = ref(false)
const progress = ref('')
const rows = ref(null)
const error = ref('')
const pickerOpen = ref(true)
const done = ref(null)

const LABEL = FIELD_LABELS

// same formatting as the profile tab, so the preview shows what you will get
function fmt(key, v) {
  if (Array.isArray(v)) return v.join('、')
  return key === 'dob' ? formatDob(v) : v
}
function ageOf(row) { return formatAge(row.profile?.dob) }

const JDB = 'https://www.javdatabase.com/idols/'

// Rows with nothing to do are the bulk of a re-run — 31 already-done
// characters would otherwise bury the two that need a decision.
const showDone = ref(false)
const actionable = computed(() =>
  (rows.value || []).filter(r => r.has_changes || (!r.ok && !r.vote?.suggest)))
const unchanged = computed(() =>
  (rows.value || []).filter(r => !actionable.value.includes(r)))
const visibleRows = computed(() =>
  showDone.value ? rows.value : actionable.value)

const expanded = ref({})
function toggleVotes(name) { expanded.value[name] = !expanded.value[name] }

// ── avatar from Gfriends ─────────────────────────────────────────────────────
// The first good choice comes preselected (the user's call); clicking it again
// clears it, which means "leave the avatar alone".
const gfUrl = c =>
  `/api/gfriends/image?c=${encodeURIComponent(c.cat)}&f=${encodeURIComponent(c.file)}`
function pickedAvatar(r) {
  const i = r.avatar?.pick
  return i == null ? null : r.avatar.choices[i]
}
function pickAvatar(r, i) {
  r.avatar.pick = r.avatar.pick === i ? null : i
  // choosing a picture on an otherwise untouched row is choosing to write it
  if (r.avatar.pick != null && !r.vote?.suggest) r.approved = true
}
function avatarTitle(c) {
  return `${c.source}　·　用「${c.by}」查到` +
    (c.weak ? '\n這個名字太短或太通用，可能是別人，所以不預選' : '')
}

/** Say yes or no to a renamed-performer suggestion. */
async function decide(row, yes) {
  try {
    await api('/api/idolprofile/reject', {
      method: 'POST',
      body: JSON.stringify({ name: row.name, slug: row.vote.slug,
                             rejected: !yes }),
    })
    row.rejected = !yes
    row.approved = yes
  } catch (e) {
    error.value = '記錄失敗：' + errText(e)
  }
}

const resolved = computed(() => (rows.value || []).filter(r => r.ok))
const suggested = computed(() => (rows.value || []).filter(r => !r.ok && r.vote?.suggest))
const unresolved = computed(() => (rows.value || []).filter(r => !r.ok && !r.vote?.suggest))
const approvedCount = computed(() =>
  (rows.value || []).filter(r => r.approved && willWrite(r)).length)
// `has_changes` is the server's view at scan time; un-picking the avatar on a
// row that had nothing else to add leaves nothing to write.
// A replacement offered by a recheck counts only once it is ticked.
function willWrite(r) {
  return (r.fields || []).some(f => f.replace ? f.approved : !f.skip) ||
    !!r.new_aliases?.length || !!pickedAvatar(r)
}

async function scan() {
  if (!selected.value.length) { error.value = '請至少選擇一位角色'; return }
  error.value = ''
  scanning.value = true
  rows.value = null
  done.value = null
  pickerOpen.value = false
  setBusy('JAV個資查詢：啟動中…')
  try {
    const { job_id } = await api('/api/idolprofile/scan', {
      method: 'POST',
      body: JSON.stringify({ names: selected.value, recheck: recheck.value }),
    })
    while (true) {
      await new Promise(r => setTimeout(r, 700))
      const j = await api(`/api/dedup/job/${job_id}`)
      progress.value = j.progress || ''
      if (j.progress) setBusy('JAV個資查詢：' + j.progress)
      if (j.status === 'done') {
        // resolved rows start approved; a renamed-performer suggestion never
        // does — that one needs a human to say "yes, same person"
        // An undecided row can still carry an avatar found by the folder's
        // own name; with one preselected it starts approved like the rest.
        rows.value = j.result.map(r => ({
          ...r,
          approved: !!r.ok || (!r.vote?.suggest && r.avatar?.pick != null),
        }))
        break
      }
      if (j.status === 'error') { error.value = j.error || '查詢失敗'; break }
    }
  } catch (e) {
    error.value = '查詢失敗：' + errText(e)
  } finally {
    scanning.value = false
    progress.value = ''
    setBusy('')
  }
}

async function apply() {
  const picked = (rows.value || []).filter(r => r.approved && willWrite(r))
  if (!picked.length) return
  const names = picked.map(r => r.name).join('、')
  const avatars = picked.filter(r => pickedAvatar(r)).length
  const swaps = picked.filter(r => pickedAvatar(r) && r.avatar.current).length
  const fieldSwaps = picked.reduce((n, r) =>
    n + (r.fields || []).filter(f => f.replace && f.approved).length, 0)
  if (!confirm(`將寫入 ${picked.length} 位角色的個人資料：\n\n${names}\n\n` +
               (avatars ? `其中 ${avatars} 位會設定圖庫的大頭照` +
                          (swaps ? `（${swaps} 位是取代目前的）` : '') +
                          `，存進角色資料夾的 .vam 子資料夾。\n` : '') +
               (fieldSwaps ? `會取代 ${fieldSwaps} 個已有的欄位（你勾選的），其餘已有內容的欄位跳過。`
                           : `已有內容的欄位一律跳過，不會覆蓋。`))) return
  applying.value = true
  try {
    const r = await api('/api/idolprofile/apply', {
      method: 'POST',
      body: JSON.stringify({ rows: picked.map(x => {
        const c = pickedAvatar(x)
        return { ...x, avatar: undefined,
                 // what the scan saw: the server refuses if it has changed since
                 avatar_pick: c ? { cat: c.cat, file: c.file,
                                    replaces: x.avatar.current || '' } : null }
      }) }),
    })
    done.value = r
    for (const w of r.written) {
      const row = rows.value.find(x => x.name === w.name)
      if (row) { row.written = true; row.approved = false; row.avatarSet = w.avatar }
    }
    const set = r.written.filter(w => w.avatar).length
    if (set) store.charsVersion++
    setStatus(`已寫入 ${r.written.length} 位角色的個人資料` +
              (set ? `，其中 ${set} 位設定了大頭照` : ''), 'ok')
  } catch (e) {
    error.value = '寫入失敗：' + errText(e)
  } finally { applying.value = false }
}
</script>

<template>
  <div class="panel">

      <!-- See MovieTitlePanel: the toggle above the chooser was describing a
           screen that was not there. The bar below replaces it, and only
           after a scan, when the results need the height. -->
      <button v-if="!pickerOpen" class="scopebar" @click="pickerOpen = true">
        <Icon name="chevronRight" :size="13" />
        範圍：已選 {{ selected.length }} 位角色
        <span class="chg">重新選擇</span>
      </button>

      <!-- `v-if`, not `v-show`: CharPicker fetches each character's
           completion state on mount, so re-opening it after a scan has
           to re-mount or the "未處理" counts still show the work you
           just did as outstanding. -->
      <div v-if="pickerOpen" class="scope">
        <CharPicker :disabled="scanning" @update:selected="v => selected = v" />
        <div class="row">
          <button class="btn" :disabled="scanning || !selected.length" @click="scan">
            <Icon name="search" :size="14" /> 開始查詢
          </button>
          <label class="recheck" title="已有的欄位會列出「目前 → 新值」，勾了才取代；已有大頭照的也列出圖庫候選，不預選">
            <input type="checkbox" v-model="recheck" :disabled="scanning" />
            也查詢已有的個資與大頭照
          </label>
          <span class="hint">只會讀取網站，不會寫入任何檔案</span>
        </div>
      </div>

      <div v-if="scanning" class="prog">
        <Icon name="spinner" :size="13" class="spin" /> {{ progress || '查詢中…' }}
        <span class="hint">每個請求間隔 1 秒，這是對來源站的禮貌</span>
      </div>
      <div v-if="error" class="err">{{ error }}</div>

      <!-- Hidden while the picker is open. Both panes scroll, and with no
           priority between them the browser splits the height by content —
           an 84-group report left the 745-row folder list a visible sliver.
           You are either setting up a run or reviewing the last one. -->
      <div v-if="rows && !pickerOpen" class="results">
        <div class="bar">
          <span>可寫入 <b>{{ resolved.length }}</b></span>
          <span v-if="suggested.length">· 待確認 <b class="warn">{{ suggested.length }}</b></span>
          <span v-if="unresolved.length">· 無法判斷 <b class="dim">{{ unresolved.length }}</b></span>
          <span class="spacer"></span>
          <button class="btn" :disabled="applying || !approvedCount" @click="apply">
            {{ applying ? '寫入中…' : `寫入已勾選的 ${approvedCount} 位` }}
          </button>
        </div>

        <div class="list">
          <div v-for="r in rows" :key="r.name" class="row-item"
               :class="{ nogo: !r.ok && !r.vote?.suggest, wrote: r.written }">
            <label class="head">
              <!-- a suggestion is approved with its own 確認/不是 buttons;
                   two controls for one decision reads as a bug -->
              <input v-if="r.has_changes && !r.vote?.suggest" type="checkbox"
                     v-model="r.approved" :disabled="applying || r.written" />
              <Icon v-else-if="r.vote?.suggest" name="warn" :size="13" class="warnic" />
              <b>{{ r.name }}</b>
              <template v-if="r.ok || r.vote?.suggest">
                <Icon name="chevronRight" :size="12" />
                <!-- the fastest way to settle "is this the same person" is to
                     look at her page, so link straight to it -->
                <a class="perf" :href="JDB + r.vote.slug + '/'" target="_blank"
                   rel="noopener noreferrer" @click.stop
                   :title="'在 javdatabase 開啟'">{{ r.performer || r.vote.name
                   }}<Icon name="link" :size="10" /></a>
                <span v-if="r.romaji" class="rom">{{ r.romaji }}</span>
                <button class="vote" @click.prevent.stop="toggleVotes(r.name)">
                  {{ r.vote.n }}/{{ r.vote.checked }} 部
                  <Icon :name="expanded[r.name] ? 'chevronDown' : 'chevronRight'" :size="10" />
                </button>
              </template>
              <span v-if="r.written" class="ok">已寫入</span>
            </label>

            <div v-if="expanded[r.name]" class="votes">
              <span v-for="v in r.vote.votes" :key="v.slug" class="v">
                <a :href="JDB + v.slug + '/'" target="_blank"
                   rel="noopener noreferrer">{{ v.name }}</a> ×{{ v.n }}
              </span>
            </div>

            <!-- a strong majority under a different name: often the same
                 performer after a rename, so it is offered, never assumed -->
            <div v-if="r.vote?.suggest" class="suggest">
              <Icon name="warn" :size="12" />
              <span>{{ r.vote.reason }}</span>
              <template v-if="!r.written">
                <button class="btn xs" :class="{ on: r.approved }"
                        @click="decide(r, true)">確認是同一人</button>
                <button class="btn ghost xs" :class="{ no: r.rejected }"
                        @click="decide(r, false)">不是</button>
              </template>
              <span v-if="r.rejected" class="rejnote">已記錄為否決，下次仍會列出</span>
            </div>
            <div v-if="r.vote?.suggest && r.approved" class="willdo">
              <Icon name="checked" :size="12" /> 已確認 —— 按上方「寫入」就會存入這位
              演員的個資，並把「{{ r.vote.name }}」加進別名
            </div>
            <div v-else-if="!r.ok" class="why">{{ r.vote?.reason || '無法判斷' }}</div>

            <div v-if="r.profile" class="fields">
              <span v-if="ageOf(r)" class="f"><i>年齡</i>{{ ageOf(r) }}</span>
              <template v-for="f in r.fields" :key="f.key">
                <!-- a recheck: the value differs from what is there, and only
                     a tick replaces it -->
                <label v-if="f.replace" class="f repl" :class="{ on: f.approved }">
                  <input type="checkbox" v-model="f.approved"
                         :disabled="applying || r.written"
                         @change="f.approved && !r.vote?.suggest && (r.approved = true)" />
                  <i>{{ LABEL[f.key] || f.key }}</i>
                  <s>{{ fmt(f.key, f.current) }}</s> → {{ fmt(f.key, f.value) }}
                </label>
                <span v-else class="f" :class="{ skip: f.skip }">
                  <i>{{ LABEL[f.key] || f.key }}</i>{{ fmt(f.key, f.value) }}
                  <em v-if="f.skip">已有，跳過</em>
                </span>
              </template>
            </div>
            <div v-if="r.new_aliases?.length" class="alias">
              別名將新增：{{ r.new_aliases.join('、') }}
            </div>

            <!-- Offered when the character has no avatar yet, or on a recheck
                 (then the current one is shown first and nothing is
                 preselected). The pictures come through the server
                 (/api/gfriends/image), so the browser never contacts GitHub. -->
            <div v-if="r.avatar && !r.written" class="avatars">
              <span class="alab">大頭照</span>
              <span v-if="r.avatar.current_path" class="av cur" title="目前的大頭照">
                <img :src="imageThumbUrl(r.avatar.current_path)" alt="" />
                <span class="src">目前</span>
              </span>
              <button v-for="(c, i) in r.avatar.choices" :key="c.cat + c.file"
                      class="av" :class="{ on: r.avatar.pick === i, weak: c.weak }"
                      :title="avatarTitle(c)" :disabled="applying"
                      @click="pickAvatar(r, i)">
                <img :src="gfUrl(c)" loading="lazy" alt="" />
                <span class="src">{{ c.source }}</span>
                <Icon v-if="r.avatar.pick === i" name="checked" :size="14" class="tick" />
              </button>
              <span v-if="r.avatar.pick == null" class="hint">{{ r.avatar.current
                ? '未選 —— 維持目前的大頭照' : '未選 —— 不設定大頭照' }}</span>
            </div>
            <div v-else-if="r.avatarSet" class="alias">
              大頭照已設定（來源 {{ pickedAvatar(r)?.source }}）
            </div>
            <div v-else-if="r.written && pickedAvatar(r)" class="alias">
              大頭照沒有設定：查詢之後大頭照已經被改過，不覆蓋
            </div>
          </div>
        </div>

        <div v-if="done" class="donebox">
          寫入完成：{{ done.written.length }} 位
          <span v-if="done.errors.length" class="err">
            ，{{ done.errors.length }} 位失敗</span>
          <div v-if="done.untranslated?.length" class="hint">
            未收錄中文翻譯（原樣寫入）：{{ done.untranslated.join('、') }}
          </div>
        </div>
      </div>
    
  
  </div>
</template>

<style scoped>
.scopebar { display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 8px 0; background: none; border: none;
  border-bottom: 1px solid #313148; color: var(--dim); font-size: 12px;
  cursor: pointer; text-align: left; }
.scopebar:hover { color: var(--text); }
.scopebar .chg { margin-left: auto; color: var(--accent); }
/* The modal shell lives in JavDialog.vue now; this file is
   one tab of it. */
.panel { display: contents; }
.spacer { flex: 1; }
.hint { color: var(--dim); font-size: 11px; }
.scope { background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; padding: 12px 14px; }
.row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.prog { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.err { color: var(--danger, #e66); font-size: 12px; }
.results { display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
.bar { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.bar b { color: var(--accent); }
.bar b.warn { color: #e9b949; }
.bar b.dim { color: var(--dim); }
.list { overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.row-item { border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; }
.row-item.nogo { opacity: .6; }
.row-item.wrote { border-color: #2c6; }
.head { display: flex; align-items: center; gap: 6px; font-size: 12px;
  cursor: pointer; }
.rom { color: var(--dim); font-size: 11px; }
.vote { color: var(--dim); font-size: 11px; margin-left: auto;
  background: none; border: none; cursor: pointer; display: inline-flex;
  align-items: center; gap: 2px; }
.vote:hover { color: var(--text); }
.perf { color: var(--accent); display: inline-flex; align-items: center; gap: 2px; }
.ok { color: #2c6; font-size: 11px; }
.suggest { color: #e9b949; font-size: 11px; margin: 4px 0 0 20px;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.btn.xs { font-size: 11px; padding: 1px 8px; }
.btn.xs.on { background: var(--accent); color: #fff; }
.btn.xs.no { border-color: #e88; color: #e88; }
.rejnote { color: var(--dim); }
.warnic { color: #e9b949; }
.willdo { display: flex; align-items: center; gap: 4px; }
.willdo { color: var(--dim); font-size: 11px; margin: 3px 0 0 20px; }
.votes { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px;
  color: var(--dim); margin: 4px 0 0 20px; }
.votes a { color: var(--accent); }
.collapsed { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--dim); }
.lnk { background: none; border: none; color: var(--accent); font-size: 11px;
  cursor: pointer; padding: 0; }
.why { color: var(--dim); font-size: 11px; margin: 3px 0 0 20px; }
.fields { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 5px 0 0 20px; }
.f { font-size: 11px; }
.f i { color: var(--dim); font-style: normal; margin-right: 5px; }
.f.skip { opacity: .5; }
.f em { color: #e9b949; font-style: normal; font-size: 10px; margin-left: 4px; }
.alias { font-size: 11px; color: var(--dim); margin: 4px 0 0 20px; }
/* Gfriends choices: portrait thumbnails, the chosen one ringed */
.avatars { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 6px;
           margin: 6px 0 0 20px; }
.avatars .alab { font-size: 11px; color: var(--dim); align-self: center; }
.avatars .hint { font-size: 11px; color: var(--dim); align-self: center; }
.av { position: relative; width: 64px; padding: 0; border: 2px solid transparent;
      border-radius: 6px; background: var(--card); cursor: pointer; overflow: hidden; }
.av img { display: block; width: 100%; height: 84px; object-fit: cover;
          object-position: 50% 25%; }
.av .src { display: block; font-size: 9px; color: var(--dim); white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; padding: 1px 2px; }
.av.on { border-color: var(--accent); }
.av.cur { cursor: default; border-color: var(--line); margin-right: 6px; }
.av.weak { opacity: .55; }
.recheck { display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
           cursor: pointer; }
.f.repl { cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
.f.repl s { color: var(--dim); }
.f.repl.on { color: var(--accent); }
.av.weak.on { opacity: 1; }
.av .tick { position: absolute; top: 3px; right: 3px; color: var(--accent);
            background: #000a; border-radius: 50%; }
.donebox { font-size: 12px; border-top: 1px solid var(--line); padding-top: 8px; }
</style>
