<script setup>
/**
 * Import a saved performer page — seesaawiki (Japan) or IAFD (the West).
 *
 * Two parsers behind one upload box: the user should not have to know which
 * one their saved file needs. The server sniffs it and the panel renders
 * whichever proposal came back; the two sources agree on the flow (preview,
 * confirm, write) and on almost nothing else.
 *
 * This is the only working source of original Japanese film titles —
 * javdatabase gives machine-translated English, and both DMM and javlibrary
 * are closed to us. The page also carries the performer's other stage names
 * and the cast of each release.
 *
 * A file rather than a URL, for now: the user downloads the page themselves,
 * so there is no robots.txt to respect, no rate limit to honour, nothing to
 * break when the site changes its markup, and no question about coverage —
 * whatever they hand over is what gets read.
 *
 * Nothing is written until the preview has been confirmed. `person_info.json`
 * lives on the NAS where git cannot protect it, which is the same reason the
 * catalogue lookup works this way.
 */
import { ref, computed } from 'vue'
import { api, errText } from '../api'
import { setStatus, can } from '../store'
import { sectionName } from '../wikisections'
import { buildApplyBody } from '../wikiapply'
import Icon from './Icon.vue'
import { t, N_ } from '../i18n'
import { ts } from '../serverstrings.js'

const pages = ref([])          // preview results, one per uploaded file
const busy = ref(false)
const error = ref('')
const fileInput = ref(null)
const applied = ref({})        // file name → what was written

// Per page, what the user has decided. Defaults follow the evidence: a page
// whose catalogue numbers appear in a folder is that person, and one that
// matches by name alone is a question, not an answer.
const choice = ref({})

defineExpose({ busy })

function bestMatch(p) {
  return p.matches?.[0] || null
}

// The wiki proposal carries a path per candidate; the IAFD one carries only a
// name, because it scores against the cached search index (which is keyed by
// character, not by folder). The server fills `match_paths` for it.
function matchPath(p, m) {
  return m?.path || (p.match_paths || {})[m?.name] || ''
}

function initChoice(p, i) {
  const m = bestMatch(p)
  const confident = !!(m && m.score > 0)
  if (p.site === 'avbase.net') {
    const talent = p.kind === 'talent'
    // Fill-only on the server, so the table offers are on by default; what
    // touches person_info.json (aliases, profile) follows the match, as in
    // the wiki import. A work page names no performer to file anything under.
    choice.value[i] = {
      folder: talent && confident ? m.path : '',
      titles: catalogCount(p) > 0,
      cast: Object.keys(p.cast || {}).length > 0,
      aliases: talent && confident && (p.aliases || []).length > 0,
      profile: talent && confident && Object.keys(p.profile || {}).length > 0,
      relations: (p.relations || []).length > 0,
    }
    return
  }
  if (p.site === 'iafd.com') {
    // The folder is found by content (this page is titled `Rosalie`, the folder
    // is `Elena Varga`), so a match here is evidence, not a coincidence.
    choice.value[i] = {
      folder: confident ? (matchPath(p, m) || '') : '',
      profile: confident && Object.keys(p.profile || {}).length > 0,
      released: confident && (p.matched || []).length > 0,
    }
    return
  }
  choice.value[i] = {
    folder: confident ? m.path : '',
    titles: Object.keys(p.titles || {}).length + catalogCount(p) > 0,
    // Aliases touch person_info.json on the NAS, so they follow the match
    // rather than being on by default.
    aliases: confident,
    cast: Object.keys(p.cast || {}).length > 0,
  }
}

async function pick(e) {
  const files = [...(e.target.files || [])]
  if (!files.length) return
  error.value = ''
  busy.value = true
  pages.value = []
  applied.value = {}
  try {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    // No Content-Type header on purpose: the browser has to set the multipart
    // boundary itself, and setting it by hand produces a request the server
    // cannot parse.
    const d = await api('/api/wiki/preview', { method: 'POST', body: fd })
    pages.value = d.pages
    pages.value.forEach((p, i) => { if (!p.error) initChoice(p, i) })
  } catch (e2) {
    error.value = t('讀取失敗：') + errText(e2)
  } finally {
    busy.value = false
    if (fileInput.value) fileInput.value.value = ''   // let the same file be re-picked
  }
}

// what each verdict means for the person holding both files (core/avbase.py)
// keys: shown through t()
const REL_KIND = {
  original: N_('原片 → 擷取：有原片就不用收'),
  uncut: N_('完整收錄的總集編'),
  digest: N_('精選總集編：原片比較完整'),
  unknown: N_('缺發行日，無法判斷'),
}
const PROFILE_LABEL = { dob: N_('生日'), height: N_('身高'), measurements: N_('三圍'),
                        cup: N_('罩杯'), birthplace: N_('出生地') }
const profLabel = k => PROFILE_LABEL[k] ? t(PROFILE_LABEL[k]) : k

// The relations worth reading: the ones touching a file you have. A
// compilation's links to other performers' films are stored all the same,
// but listing two thousand of them would bury these.
function relShown(p) {
  return (p.relations || []).filter(r => r.have_parent || r.have_child)
    .sort((a, b) => (b.have_parent && b.have_child) - (a.have_parent && a.have_child))
}

function profileText(profile) {
  return Object.entries(profile || {})
    .map(([k, v]) => `${profLabel(k)} ${v}`).join(t('、'))
}

function writtenText(w) {
  if (!w) return ''
  const bits = [t('作品目錄 {n} 部（庫裡的片名 {titles}、發行日 {released}）',
                  { n: w.catalog || 0, titles: w.titles, released: w.released }),
                t('共演對照 {n}', { n: w.cast }), t('收錄關係 {n}', { n: w.relations })]
  if (w.aliases) bits.push(t('藝名 {n}（{list}）', { n: w.aliases, list: (w.added_aliases || []).join(t('、')) }))
  if ((w.profile || []).length) {
    bits.push(t('個資 {list}', { list: w.profile.map(profLabel).join(t('、')) }))
  }
  return bits.join(t('、'))
}

const titleCount = p => Object.keys(p.titles || {}).length
const replacedCount = p => Object.keys(p.replacing || {}).length
// works on the page with a number and a title or date: what goes into the
// shared catalogue, so films added later get them too (core/catalog.py)
const catalogCount = p => (Array.isArray(p.works) ? p.works : [])
  .filter(w => w.i && (w.t || w.d)).length

async function apply(p, i) {
  const c = choice.value[i]
  if (!c) return
  busy.value = true
  error.value = ''
  try {
    // Built in ../wikiapply.js so that "did we remember to send every field
    // the server reads" is a test rather than a thing to notice by eye. Two
    // fields were missing before it existed.
    const d = await api('/api/wiki/apply', {
      method: 'POST', body: JSON.stringify(buildApplyBody(p, c)),
    })
    applied.value = { ...applied.value, [i]: d }
    if (p.site === 'avbase.net') {
      setStatus(t('AVBASE 匯入：') + writtenText(d.written), 'ok')
    } else if (p.site === 'iafd.com') {
      const w = d.written || {}
      setStatus(t('IAFD 匯入：個人資料 {n} 欄', { n: (w.profile || []).length }) +
                (w.file_released ? t('、發行日 {n} 筆', { n: w.file_released }) : ''), 'ok')
    } else {
      setStatus(t('WIKI 匯入：{n} 個日文片名', { n: d.titles }) +
                (d.aliases ? t('、{n} 個別名', { n: d.aliases }) : ''), 'ok')
    }
  } catch (e2) {
    error.value = t('寫入失敗：') + errText(e2)
  } finally { busy.value = false }
}

// ── after writing: mark the page's co-stars in bulk ────────────────────────
// The cast table was just written; this matches each film's cast to character
// folders (names and aliases) and offers the result. Only adds; can be undone
// while the panel is open. Cast with no folder are listed, not written — the
// player shows the whole cast anyway.
const fill = ref({})             // page index → { busy, preview, done, error }
const pageIds = p => (Array.isArray(p.works) ? p.works : []).map(w => w.i).filter(Boolean)
const fillItems = pv => pv.rows.map(r => ({ path: r.path, folders: r.add.map(a => a.folder) }))

async function fillPreview(p, i) {
  fill.value = { ...fill.value, [i]: { busy: true } }
  try {
    const d = await api('/api/costar/fill/preview', {
      method: 'POST', body: JSON.stringify({ ids: pageIds(p) }),
    })
    fill.value = { ...fill.value, [i]: { preview: d } }
  } catch (e) {
    fill.value = { ...fill.value, [i]: { error: t('讀取失敗：') + errText(e) } }
  }
}

async function fillApply(i, undo = false) {
  const f = fill.value[i]
  fill.value = { ...fill.value, [i]: { ...f, busy: true } }
  try {
    const d = await api('/api/costar/fill', {
      method: 'POST', body: JSON.stringify({ items: fillItems(f.preview), undo }),
    })
    fill.value = { ...fill.value, [i]: { ...f, busy: false, done: undo ? null : d } }
    setStatus(undo ? t('共演：已復原 {n} 筆', { n: d.marks }) : t('共演：加入 {n} 筆（{videos} 部影片）', { n: d.marks, videos: d.videos }), 'ok')
  } catch (e) {
    fill.value = { ...fill.value, [i]: { ...f, busy: false, error: t('寫入失敗：') + errText(e) } }
  }
}

const anyChosen = computed(() =>
  pages.value.some((p, i) => !p.error && !applied.value[i]))
</script>

<template>
  <div class="panel">
    <div class="intro">
      {{ t('把') }}<b>{{ t('演員頁面存成 HTML') }}</b>{{ t('（瀏覽器的「另存新檔 → 網頁，完整」）再丟進來，可以一次選多個檔案。丟進來會自動辨識是哪個站：') }}
      <br><b>seesaawiki</b>{{ t('（日本）→ 匯入藝名、番號的日文片名、發行日') }}
      <br><b>IAFD</b>{{ t('（歐美）→ 匯入個人資料、以及比對得到的作品發行日') }}
      <br><b>AVBase</b>{{ t('（日本，作品頁或演員頁）→ 片名、發行日、藝名、個資，以及') }}<b>{{ t('收錄關係') }}</b>
      {{ t('（哪部片是從哪部剪出來的）。只補空的，不會蓋掉已有的資料') }}
      <br>{{ t('兩邊的作品清單合成一份（角色影片頁的 ⓘ）；片名以 wiki 為準') }}
    </div>

    <label class="pickbtn">
      <input ref="fileInput" type="file" accept=".html,.htm" multiple
             :disabled="busy" @change="pick" />
      <Icon name="package" :size="14" /> {{ t('選擇已存檔的演員頁面…') }}
    </label>

    <div v-if="busy" class="hint">{{ t('處理中…') }}</div>
    <div v-if="error" class="err">{{ error }}</div>

    <div v-for="(p, i) in pages" :key="p.file" class="page">
      <div v-if="p.error" class="err">
        <b>{{ p.file }}</b> — {{ ts(p.error) }}
      </div>
      <!-- IAFD: same dialog, same preview-then-confirm flow, but it writes a
           bio and some release years and nothing else. Kept as its own block
           rather than threading v-ifs through the wiki one — the two sources
           agree on almost nothing. -->
      <template v-else-if="p.site === 'iafd.com'">
        <div class="phead">
          <span class="pname">{{ p.name }}</span>
          <span class="pmeta">{{ t('IAFD 收錄 {n} 部', { n: p.works }) }}</span>
          <span class="spacer"></span>
          <span class="src ib">iafd.com</span>
        </div>

        <div class="row">
          <span class="lab">{{ t('對應角色') }}</span>
          <select v-model="choice[i].folder" :disabled="busy">
            <option value="">{{ t('（不寫入）') }}</option>
            <option v-for="m in p.matches" :key="m.name"
                    :value="(p.match_paths || {})[m.name] || ''">
              {{ m.name }} — {{ t('作品對上 {n} 部', { n: m.score }) }}
            </option>
          </select>
        </div>
        <div v-if="!p.matches.length" class="warn ib">
          <Icon name="warn" :size="12" />
          {{ t('沒有任何資料夾的影片對得上這一頁的作品。IAFD 只收錄片商發行的作品，社群媒體與自製內容它沒有 —— 也可能這一頁根本不是這個人。') }}
        </div>
        <div v-else class="shared">
          {{ t('對上的作品：') }}{{ bestMatch(p).shared.join(t('、')) }}
        </div>

        <label class="row" v-if="Object.keys(p.profile || {}).length">
          <input type="checkbox" v-model="choice[i].profile" :disabled="busy" />
          <span>{{ t('寫入個人資料（{list}）', { list: Object.keys(p.profile).map(profLabel).join(t('、')) }) }}</span>
        </label>
        <div v-if="Object.keys(p.profile_existing || {}).length" class="shared">
          {{ t('已經填過、不會被覆蓋：') }}{{ Object.keys(p.profile_existing).map(profLabel).join(t('、')) }}
        </div>
        <div v-if="Object.keys(p.extra || {}).length" class="shared">
          {{ t('這一頁另外有') }}
          <b>{{ Object.keys(p.extra).join(t('、')) }}</b>
          {{ t('—— 目前不寫入（個人資料的欄位是固定的白名單）') }}
        </div>

        <label class="row" v-if="(p.matched || []).length">
          <input type="checkbox" v-model="choice[i].released" :disabled="busy" />
          <span>{{ t('寫入發行日：{n} 個檔案（資料夾共 {total} 部）', { n: p.matched.length, total: p.files_total }) }}</span>
        </label>
        <div v-if="(p.matched || []).length" class="shared">
          {{ t('比對是用') }}<b>{{ t('片名') }}</b>{{ t('做的，不是番號 —— 分不出來的一律不配對。') }}
          {{ t('另有 {n} 部作品在你的庫裡找不到對應檔案。', { n: p.unmatched_works }) }}
        </div>
        <details v-if="(p.matched || []).length" class="mlist">
          <summary>{{ t('看比對結果（{n}）', { n: p.matched.length }) }}</summary>
          <div v-for="m in p.matched" :key="m.path" class="mrow">
            <span class="my">{{ m.year }}</span>
            <span class="mt">{{ m.title }}</span>
            <span class="mf" :title="m.path">{{ m.name }}</span>
          </div>
        </details>

        <div class="prow">
          <button class="btn" :disabled="busy || applied[i]" @click="apply(p, i)">
            {{ applied[i] ? t('已寫入') : t('寫入') }}</button>
          <span v-if="applied[i]" class="ok ib">
            <Icon name="check" :size="12" />
            {{ t('個人資料 {n} 欄、發行日 {m} 筆', { n: (applied[i].written?.profile || []).length, m: applied[i].written?.file_released || 0 }) }}
          </span>
        </div>
      </template>

      <!-- AVBase: the wiki's flow, fill-only, plus the relations between
           works — the one thing no other source has. -->
      <template v-else-if="p.site === 'avbase.net'">
        <div class="phead">
          <span class="pname">{{ p.name }}</span>
          <span class="pmeta">{{ p.kind !== 'talent'
            ? t('作品頁：只含這一部與它的原片／衍生片')
            : p.page ? t('演員頁第 {page} 頁 · 這頁 {n} 部（共 {total} 部）', { page: p.page, n: p.works.length, total: p.total })
                     : t('演員頁 · {n} 部（AVBase 上共 {total} 部）', { n: p.works.length, total: p.total }) }}</span>
          <span class="spacer"></span>
          <a v-if="p.url" :href="p.url" target="_blank" rel="noopener noreferrer"
             class="src ib"><Icon name="link" :size="11" /> AVBase</a>
        </div>

        <template v-if="p.kind === 'talent'">
          <div class="row">
            <span class="lab">{{ t('對應角色') }}</span>
            <select v-model="choice[i].folder" :disabled="busy">
              <option value="">{{ t('（不寫入藝名、個資與收錄統計）') }}</option>
              <option v-for="m in p.matches" :key="m.path" :value="m.path">
                {{ m.name }} — {{ m.score ? t('番號重疊 {n} 個', { n: m.score }) : t('只有名字相符') }}
              </option>
            </select>
          </div>
          <div v-if="bestMatch(p) && !bestMatch(p).score" class="warn ib">
            <Icon name="warn" :size="12" />
            {{ t('這一頁的番號跟資料夾一個都對不上，只有名字相同 —— 請確認過再寫入藝名與個資。') }}
          </div>
          <div v-else-if="!bestMatch(p)" class="warn ib">
            <Icon name="warn" :size="12" /> {{ t('找不到對應的角色資料夾，藝名與個資會跳過。') }}
          </div>
        </template>

        <div class="opts">
          <label class="chk">
            <input type="checkbox" v-model="choice[i].titles"
                   :disabled="busy || !catalogCount(p)" />
            {{ t('片名與發行日：') }}<b>{{ catalogCount(p) }}</b> {{ t('部存進作品目錄') }}
            <span class="dim">{{ t('（之後才下載的也會自動帶入）') }}</span>
            · {{ t('庫裡有的') }} <b>{{ titleCount(p) }}</b> {{ t('部會顯示日文片名、') }}
            <b>{{ Object.keys(p.released || {}).length }}</b> {{ t('部補上發行日') }}
            <span v-if="replacedCount(p)" class="dim">
              · {{ t('其中 {n} 個原本顯示英文', { n: replacedCount(p) }) }}</span>
            <div class="dim">{{ t('手打的、已經有日文的（wiki、DMM）一律優先，這裡只補沒有日文的。') }}</div>
          </label>
          <label class="chk">
            <input type="checkbox" v-model="choice[i].cast"
                   :disabled="busy || !Object.keys(p.cast || {}).length" />
            {{ t('共演者對照表') }} <b>{{ Object.keys(p.cast || {}).length }}</b> {{ t('部') }}
            <span class="dim">{{ t('只建立「番號 → 演員」的對照，用來建議共演') }}</span>
          </label>
          <label v-if="p.kind === 'talent'" class="chk">
            <input type="checkbox" v-model="choice[i].aliases"
                   :disabled="busy || !choice[i].folder || !p.aliases.length" />
            {{ t('藝名') }} <b>{{ p.aliases.length }}</b> {{ t('個（只增不覆蓋）') }}
            <span class="dim">{{ p.aliases.join(t('、')) }}</span>
          </label>
          <label v-if="p.kind === 'talent' && Object.keys(p.profile || {}).length" class="chk">
            <input type="checkbox" v-model="choice[i].profile"
                   :disabled="busy || !choice[i].folder" />
            {{ t('個資') }} <span class="dim">{{ profileText(p.profile) }}</span>
          </label>
          <div v-if="Object.keys(p.profile_existing || {}).length" class="shared">
            {{ t('已經填過、不會被覆蓋：') }}{{ Object.keys(p.profile_existing)
              .map(profLabel).join(t('、')) }}
          </div>
          <label class="chk">
            <input type="checkbox" v-model="choice[i].relations"
                   :disabled="busy || !(p.relations || []).length" />
            {{ t('收錄關係') }} <b>{{ (p.relations || []).length }}</b> {{ t('筆') }}
            <span v-if="p.rel_counts?.hers" class="dim">
              · {{ t('兩部都有她出演的 {n} 筆，{m} 筆判斷得出來', { n: p.rel_counts.hers, m: p.rel_counts.hers_judged }) }}</span>
            <span v-if="p.rel_counts?.both" class="dim">
              · {{ t('其中 {n} 組兩部你都有', { n: p.rel_counts.both }) }}<template
                v-if="p.rel_counts.redundant">{{ t('，{n} 組可以只留原片', { n: p.rel_counts.redundant }) }}</template></span>
          </label>
        </div>

        <details v-if="(p.relations || []).length" class="mlist">
          <summary>{{ t('看跟你的檔案有關的收錄關係（{n}）', { n: relShown(p).length }) }}</summary>
          <div v-for="r in relShown(p).slice(0, 300)" :key="r.parent + '>' + r.child" class="mrow rel">
            <span class="mt" :class="{ own: r.have_parent }">{{ r.parent }}</span>
            <span class="my">{{ r.parent_date || t('日期不明') }}</span>
            <span class="arrow">⊃</span>
            <span class="mt" :class="{ own: r.have_child }">{{ r.child }}</span>
            <span class="my">{{ r.child_date || t('日期不明') }}</span>
            <span class="kind" :class="r.kind">{{ REL_KIND[r.kind] ? t(REL_KIND[r.kind]) : r.kind }}</span>
          </div>
          <div v-if="relShown(p).length > 300" class="dupnote">{{ t('只列前 300 筆。') }}</div>
          <div class="dupnote">{{ t('綠色＝你庫裡有的番號。「⊃」左邊的影片內容包含右邊。其餘的關係（多半是總集編收錄的其他女優作品）一樣會存起來。') }}</div>
        </details>

        <div v-if="applied[i]" class="done ib">
          <Icon name="check" :size="14" />
          {{ t('已寫入：') }}{{ writtenText(applied[i].written) }}
        </div>
        <div v-else class="actions">
          <button class="btn ib" :disabled="busy" @click="apply(p, i)">
            <Icon name="check" :size="14" /> {{ t('寫入這一頁') }}
          </button>
        </div>
      </template>

      <template v-else>
        <div class="phead">
          <span class="pname">{{ p.name }}</span>
          <span class="pmeta">{{ t('wiki 收錄 {n} 部', { n: p.summary?.total ?? p.works }) }}
            · {{ t('{n} 個藝名', { n: p.aliases.length }) }}</span>
          <span class="spacer"></span>
          <a v-if="p.url" :href="p.url" target="_blank" rel="noopener noreferrer"
             class="src ib"><Icon name="link" :size="11" /> {{ t('來源') }}</a>
        </div>

        <!-- Which character this is. Catalogue overlap is the evidence; a
             matching name on its own is how /api/javdb/seed went wrong. -->
        <div class="row">
          <span class="lab">{{ t('對應角色') }}</span>
          <select v-model="choice[i].folder" :disabled="busy">
            <option value="">{{ t('（不寫入別名）') }}</option>
            <option v-for="m in p.matches" :key="m.path" :value="m.path">
              {{ m.name }}
              — {{ m.score ? t('番號重疊 {n} 個', { n: m.score }) : t('只有名字相符') }}
            </option>
          </select>
        </div>
        <div v-if="bestMatch(p) && !bestMatch(p).score" class="warn ib">
          <Icon name="warn" :size="12" />
          {{ t('這一頁的番號跟資料夾一個都對不上。名字相同可能是同名、也可能是改藝名 —— 請自己確認過再寫入別名。') }}
        </div>
        <div v-else-if="bestMatch(p)" class="shared">
          {{ t('對上的番號：') }}{{ bestMatch(p).shared.join(t('、')) }}
        </div>
        <div v-else class="warn ib">
          <Icon name="warn" :size="12" /> {{ t('找不到對應的角色資料夾。片名還是可以匯入（它跟角色無關），別名就跳過。') }}
        </div>

        <div v-if="p.summary?.sections?.length" class="secs">
          <span v-for="sec in p.summary.sections" :key="sec.name" class="sec">
            {{ sectionName(sec.name) }} <b>{{ sec.count }}</b>
          </span>
          <span v-if="p.summary.no_id" class="sec dimsec">
            {{ t('其中 {n} 部沒有番號', { n: p.summary.no_id }) }}
          </span>
        </div>

        <div class="opts">
          <label class="chk">
            <input type="checkbox" v-model="choice[i].titles"
                   :disabled="busy || !(titleCount(p) + catalogCount(p))" />
            {{ t('日文片名與發行日：庫裡有的') }} <b>{{ titleCount(p) }}</b> {{ t('個') }}
            <span v-if="titleCount(p) < catalogCount(p)" class="dim">
              · {{ t('頁面上全部 {n} 部存進作品目錄，之後才下載的也會自動帶入', { n: catalogCount(p) }) }}</span>
            <span v-if="replacedCount(p)" class="dim">
              · {{ t('其中 {n} 個會取代現有的英文片名', { n: replacedCount(p) }) }}</span>
          </label>
          <label class="chk">
            <input type="checkbox" v-model="choice[i].aliases"
                   :disabled="busy || !choice[i].folder || !p.aliases.length" />
            {{ t('別名') }} <b>{{ p.aliases.length }}</b> {{ t('個（只增不覆蓋）') }}
            <span class="dim">{{ p.aliases.join(t('、')) }}</span>
          </label>
          <label class="chk">
            <input type="checkbox" v-model="choice[i].cast" :disabled="busy" />
            {{ t('共演者對照表') }}
            <span class="dim">{{ t('只建立「番號 → 演員」的對照，不寫進任何人的別名') }}</span>
          </label>
        </div>

        <div v-if="applied[i]" class="done ib">
          <Icon name="check" :size="14" />
          {{ t('已寫入：{n} 個日文片名', { n: applied[i].titles }) }}<template
            v-if="applied[i].aliases">{{ t('、{n} 個別名（{list}）', { n: applied[i].aliases,
            list: applied[i].added_aliases?.join(t('、')) }) }}</template><template
            v-else-if="choice[i].aliases">{{ t('、別名沒有新增（都已經有了）') }}</template>
          <!-- Named, not just counted: "已經有了" and "被判斷成同一個名字"
               look the same from outside, and only the user can say whether
               the judgement was right. -->
          <div v-if="applied[i].duplicate_aliases?.length" class="dupnote">
            {{ t('已存在、沒有重複加入：') }}{{ applied[i].duplicate_aliases.join(t('、')) }}
            <span class="dim">{{ t('（舊字體與新字體視為同一個名字，例如 淺 ＝ 浅）') }}</span>
          </div>
        </div>
        <div v-else class="actions">
          <button class="btn ib" :disabled="busy" @click="apply(p, i)">
            <Icon name="check" :size="14" /> {{ t('寫入這一頁') }}
          </button>
        </div>
      </template>

      <!-- Next step once a page is written: its films' co-stars, in bulk. -->
      <div v-if="applied[i] && p.site !== 'iafd.com' && can('edit_info')" class="fillbox">
        <template v-if="!fill[i] || (!fill[i].preview && !fill[i].busy)">
          <button class="btn ghost ib" :disabled="busy" @click="fillPreview(p, i)">
            <Icon name="user" :size="13" /> {{ t('帶入共演：找出這一頁的片裡、有角色資料夾的共演者') }}
          </button>
          <span v-if="fill[i]?.error" class="errtxt">{{ fill[i].error }}</span>
        </template>
        <div v-else-if="fill[i].busy && !fill[i].preview" class="dim">{{ t('找共演中…') }}</div>
        <template v-else>
          <div class="fillsum">
            {{ t('共演：這一頁的片你有') }} <b>{{ fill[i].preview.checked }}</b> {{ t('部，其中') }}
            <b>{{ fill[i].preview.rows.length }}</b> {{ t('部可以加') }}
            <b>{{ fill[i].preview.marks }}</b> {{ t('筆共演') }}
          </div>
          <details v-if="fill[i].preview.rows.length" class="mlist">
            <summary>{{ t('看要加哪些（{n}）', { n: fill[i].preview.rows.length }) }}</summary>
            <div v-for="r in fill[i].preview.rows" :key="r.video_id" class="mrow">
              <span class="my">{{ r.video_id }}</span>
              <span class="mt">{{ r.lead }} ＋ {{ r.add.map(a => a.name
                + (a.matched !== a.name ? `（${a.matched}）` : '')).join(t('、')) }}</span>
            </div>
          </details>
          <div v-if="fill[i].preview.unmatched_total" class="shared">
            {{ t('另有 {n} 位演員在你的庫裡沒有角色資料夾，不會寫入；播放時共演欄下方的「演出」會列出他們，點一下就能只用名字加入。', { n: fill[i].preview.unmatched_total }) }}
            <span class="dim">{{ t('例：') }}{{ fill[i].preview.unmatched.slice(0, 8)
              .map(u => `${u.name}（${u.films}）`).join(t('、')) }}</span>
          </div>
          <div v-if="fill[i].done" class="done ib">
            <Icon name="check" :size="14" />
            {{ t('已加入 {n} 筆共演（{videos} 部影片）', { n: fill[i].done.marks, videos: fill[i].done.videos }) }}
            <button class="btn ghost sm" :disabled="fill[i].busy" @click="fillApply(i, true)">{{ t('復原') }}</button>
          </div>
          <div v-else-if="fill[i].preview.marks" class="actions">
            <button class="btn ib" :disabled="fill[i].busy" @click="fillApply(i)">
              <Icon name="check" :size="14" /> {{ t('加入 {n} 筆共演', { n: fill[i].preview.marks }) }}
            </button>
          </div>
          <span v-if="fill[i].error" class="errtxt">{{ fill[i].error }}</span>
        </template>
      </div>
    </div>

    <div v-if="pages.length && !anyChosen && !busy" class="hint">
      {{ t('全部處理完了。可以再選其他頁面。') }}
    </div>
  </div>
</template>

<style scoped>
/* `contents` so JavDialog's body is the only scroller — two nested scroll
   areas end up fighting each other for height. */
.panel { display: contents; }
.intro { color: var(--dim); font-size: 11px; line-height: 1.8;
  background: var(--card); border: 1px solid #ffffff0d; border-radius: 12px; padding: 10px 14px; }
.intro b { color: var(--text); }
.pickbtn { display: inline-flex; align-items: center; gap: 6px;
  align-self: flex-start; background: var(--card); border: 1px solid #555;
  border-radius: 6px; padding: 7px 14px; font-size: 12px; cursor: pointer; }
.pickbtn:hover { border-color: var(--accent); }
.pickbtn input { display: none; }
.hint { color: var(--dim); font-size: 11px; }
.err { color: var(--danger); font-size: 12px; }
.page { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px; }
.phead { display: flex; align-items: center; gap: 10px; }
.pname { font-size: 14px; font-weight: bold; }
.pmeta { color: var(--dim); font-size: 11px; }
.fillbox { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line);
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.fillsum { font-size: 12px; }
.errtxt { color: var(--danger); font-size: 11px; }
.fillbox .btn.sm { padding: 2px 8px; font-size: 11px; margin-left: 6px; }
.spacer { flex: 1; }
.src { color: var(--dim); font-size: 11px; text-decoration: none; }
.src:hover { color: var(--accent); }
.row { display: flex; align-items: center; gap: 8px; }
.lab { color: var(--dim); font-size: 11px; }
.row select { flex: 1; font-size: 12px; padding: 4px 6px; }
.mlist { margin: 4px 0 0 0; font-size: 11px; }
.mlist summary { color: var(--dim); cursor: pointer; }
.mrow { display: flex; gap: 8px; padding: 2px 0 2px 14px; align-items: baseline; }
.mrow .my { color: var(--dim); font-variant-numeric: tabular-nums; }
.mrow .mt { color: var(--text); white-space: nowrap; }
.mrow .mf { color: var(--dim); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; min-width: 0; }
.shared { color: var(--success); font-size: 11px; }
/* Not styled as an error: a name-only match is a question for the user, not a
   fault. It has to be impossible to miss all the same. */
.warn { color: #f9e2af; font-size: 11px; background: #2a2416;
  border: 1px solid #4a4020; border-radius: 6px; padding: 6px 9px;
  line-height: 1.7; }
.secs { display: flex; flex-wrap: wrap; gap: 4px 8px; font-size: 11px;
  color: var(--dim); }
.sec b { color: var(--text); font-weight: 600; }
.dimsec { opacity: .75; }
.opts { display: flex; flex-direction: column; gap: 6px; }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px;
  cursor: pointer; flex-wrap: wrap; }
.chk input { margin: 0; }
.chk .dim { color: var(--dim); font-size: 10px; }
.actions { display: flex; gap: 8px; }
.done { color: var(--success); font-size: 12px; background: #162416;
  border: 1px solid var(--success); border-radius: 6px; padding: 7px 10px;
  display: block; }
.dupnote { color: var(--dim); font-size: 11px; margin-top: 5px; }
.mrow.rel { align-items: center; }
.mrow.rel .mt { min-width: 92px; color: var(--dim); }
.mrow.rel .mt.own { color: var(--success); }
.arrow { color: var(--dim); }
.kind { font-size: 10px; border-radius: 6px; padding: 0 6px; margin-left: 4px;
  background: #ffffff14; color: var(--dim); white-space: nowrap; }
.kind.original, .kind.uncut { background: #1e3a28; color: var(--success); }
.kind.digest { background: #3a3220; color: #f9e2af; }
.dupnote .dim { opacity: .8; }
</style>
