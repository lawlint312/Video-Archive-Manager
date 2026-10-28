<script setup>
/**
 * 同內容不同番號: films whose footage another film here holds whole — the same
 * footage sold again under another number (AVBase's relations).
 *
 * No scan (the user's call, 2026-10-27): the relation was stored by the
 * AVBase import, so opening this tab is all it takes — the server checks
 * which numbers the library has in its cached index (/api/dedup/relations).
 *
 * Kept: the original, or the uncut compilation holding the cut. Never batch
 * deleted — every group is the user's to decide; a cut sharper than its
 * original (a 4K remaster) is marked as needing a look (relkeeper.js). A cut
 * has no 「保留此項」: keeping it would delete the original *and* the other
 * cuts, which are different films.
 */
import { ref, computed, onMounted, nextTick } from 'vue'
import { api, streamUrl, errText } from '../api'
import { can } from '../store'
import { suggestKeeper, fmtSize, fmtDur, fmtDate } from '../dedup'
import { suggestRelKeeper, REL_KIND } from '../relkeeper.js'
import { releaseFile } from '../player'
import VideoPlayer from './VideoPlayer.vue'
import Icon from './Icon.vue'

const groups = ref(null)
const stored = ref(0)            // relations on record at all
const loading = ref(false)
const error = ref('')
const playing = ref(null)

function score(g) {
  const { keeper, certain, why } = suggestRelKeeper(g.files, suggestKeeper)
  g.keeper = keeper ? keeper.path : null
  g.certain = certain
  g.why = why
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const d = await api('/api/dedup/relations')
    stored.value = d.relations
    groups.value = d.groups.map(g => {
      const grp = { ...g, files: g.files.map(f => ({ ...f, deleted: false, error: '' })) }
      score(grp)
      return grp
    })
  } catch (e) {
    error.value = '讀取失敗：' + errText(e)
  } finally { loading.value = false }
}
onMounted(load)

const live = computed(() => (groups.value || []).filter(g =>
  g.files.some(f => f.role === 'parent' && !f.deleted)
  && g.files.some(f => f.role === 'child' && !f.deleted)))
const waste = computed(() => live.value.reduce((n, g) =>
  n + g.files.filter(f => f.role === 'child' && !f.deleted)
           .reduce((m, f) => m + f.size, 0), 0))

function rowClass(g, f) {
  if (f.deleted) return 'gone'
  if (!g.keeper) return ''
  if (!g.certain) return 'unsure'
  return f.path === g.keeper ? 'keep' : f.role === 'child' ? 'del' : ''
}

async function letGo(paths) {
  if (playing.value && paths.includes(playing.value.path)) {
    await releaseFile(() => { playing.value = null })
  }
}

async function remove(g, doomed, ask) {
  if (!doomed.length || !confirm(ask)) return
  const paths = doomed.map(f => f.path)
  await letGo(paths)
  try {
    const r = await api('/api/media/delete', { method: 'POST', body: JSON.stringify({ paths }) })
    const gone = new Set(r.deleted)
    for (const f of doomed) if (gone.has(f.path)) f.deleted = true
    for (const e of r.errors || []) {
      const f = doomed.find(x => x.path === e.path)
      if (f) f.error = e.error
    }
  } catch (e) {
    for (const f of doomed) f.error = errText(e)
  }
  score(g)
}

function delOne(g, f) {
  remove(g, [f], `確定要永久刪除？\n\n${f.name}`)
}

function keepOne(g, keep) {
  // only offered on the original: the cuts and its other copies go
  const doomed = g.files.filter(f => f !== keep && !f.deleted)
  remove(g, doomed, `保留：\n  ${keep.name}\n\n刪除：\n` + doomed.map(f => '  ' + f.name).join('\n'))
}

async function closePlayer() {
  playing.value = null
  await nextTick()
}
</script>

<template>
  <div class="panel">
    <div class="intro">
      AVBase 匯入的<b>收錄關係</b>裡，兩部你都有、而且一部的內容完整包含在另一部的：
      擷取版（從原片剪出來的），或被完整收錄進總集編的。不用掃描，打開就是最新的。
      <span class="dim">精選總集編（只放了片段）和缺發行日的不列。</span>
      <button class="btn ghost sm ib" :disabled="loading" @click="load">
        <Icon name="refresh" :size="12" /> 重新整理</button>
    </div>

    <div v-if="loading && !groups" class="msg">讀取中…</div>
    <div v-else-if="error" class="msg err">{{ error }}</div>
    <div v-else-if="!stored" class="msg">
      還沒有收錄關係。到「角色查詢 → WIKI 匯入」丟進 AVBase 的作品頁或演員頁，就會有了。
    </div>
    <div v-else-if="!live.length" class="msg">
      已存的 {{ stored }} 筆收錄關係裡，沒有兩部你都有的（或都處理完了）。
    </div>

    <template v-else>
      <div class="summary">
        <b>{{ live.length }}</b> 組，擷取版／被收錄的共約 <b>{{ fmtSize(waste) }}</b>
        <span class="dim">　每一組都要自己確認，沒有批次刪除</span>
      </div>
      <div v-if="!can('delete')" class="noperm">這個帳號沒有「刪除檔案」的權限：可以看、可以播放比對，刪除要由有權限的帳號來做</div>

      <div class="results">
        <div v-for="g in groups" :key="g.relation.parent" class="card">
          <div class="relline">
            <b>{{ g.relation.parent }}</b><span class="dim">（{{ g.relation.parent_date || '日期不明' }}）</span>
            <template v-for="c in g.relation.children" :key="c.id">
              ⊃ <b>{{ c.id }}</b><span class="dim">（{{ c.date || '日期不明' }}）</span>
              <span class="relkind">{{ REL_KIND[c.kind] || c.kind }}<template
                v-if="c.via !== g.relation.parent">，經由 {{ c.via }}</template></span>
            </template>
            <span v-if="g.why" class="why">{{ g.why }}</span>
          </div>
          <template v-for="f in g.files" :key="f.path">
            <div class="row" :class="rowClass(g, f)">
              <span class="role" :class="f.role">{{ f.role === 'parent' ? '原片' : '被包含' }}</span>
              <span class="where" :title="f.label">{{ f.label }}</span>
              <span class="fn" :title="f.path">{{ f.name }}</span>
              <span class="col">{{ fmtDur(f.duration) }}</span>
              <span class="col">{{ f.width ? `${f.width}×${f.height}` : '?' }}</span>
              <span class="col">{{ fmtSize(f.size) }}</span>
              <span class="col">{{ fmtDate(f.mtime) }}</span>
              <button class="ib icon" title="播放" :disabled="f.deleted"
                      @click="playing = f"><Icon name="play" :size="12" /></button>
              <template v-if="can('delete')">
                <button class="ib icon trash" title="刪除此檔案" :disabled="f.deleted"
                        @click="delOne(g, f)"><Icon name="trash" :size="12" /></button>
                <button v-if="f.role === 'parent'" class="keepbtn" :disabled="f.deleted"
                        @click="keepOne(g, f)">保留此項</button>
              </template>
            </div>
            <div v-if="f.error" class="rowerr ib"><Icon name="warn" :size="12" /> {{ f.error }}</div>
          </template>
        </div>
      </div>
    </template>

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
/* laid out like the other two tabs: every section a flex child of the dialog */
.panel { display: contents; }
.intro { padding: 4px 16px 10px; font-size: 12px; line-height: 1.7; color: var(--text); }
.intro .btn { margin-left: 8px; }
.dim { color: var(--dim); font-size: 11px; }
.msg { color: var(--dim); padding: 30px 16px; text-align: center; font-size: 13px; }
.msg.err { color: var(--danger); }
.summary { padding: 0 16px 8px; font-size: 12px; }
.noperm { margin: 0 16px 10px; color: var(--dim); font-size: 12px; }
.results { flex: 1; min-height: 0; overflow-y: auto; padding: 0 16px 16px; }
.card { border: 1px solid #404058; border-radius: 6px; margin-bottom: 10px;
  padding: 8px 10px; background: var(--card); }
.relline { font-size: 12px; margin: 0 0 6px 2px; line-height: 1.7; }
.relkind { font-size: 10px; color: #89b4fa; margin: 0 8px 0 2px; }
.why { font-size: 10px; color: #f9e2af; margin-left: 6px; }
.row { display: flex; align-items: center; gap: 6px; padding: 3px 6px;
  border-radius: 3px; margin-bottom: 2px; }
.row.keep { background: #162416; }
.row.del { background: #2a1616; }
.row.unsure { background: #2a2010; }
.row.gone { background: #1a1a1a; }
.row.gone .fn { color: var(--dim); text-decoration: line-through; }
.role { font-size: 10px; flex: 0 0 40px; color: #89b4fa; }
.role.child { color: var(--dim); }
.where { font-size: 10px; color: var(--accent); flex: 0 0 110px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.fn { flex: 1; min-width: 0; font-size: 11px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.col { font-size: 10px; color: var(--dim); flex: 0 0 80px; }
.icon { background: none; border: none; color: var(--dim); cursor: pointer; padding: 2px 4px; }
.icon:hover:not(:disabled) { color: var(--text); }
.icon.trash:hover:not(:disabled) { color: var(--danger); }
.keepbtn { font-size: 10px; padding: 2px 8px; background: none; color: var(--success);
  border: 1px solid #2a4a2a; border-radius: 4px; cursor: pointer; }
.keepbtn:disabled, .icon:disabled { opacity: .4; cursor: default; }
.rowerr { color: var(--danger); font-size: 10px; padding: 1px 6px 4px 50px; gap: 5px; }
.btn.sm { padding: 2px 8px; font-size: 11px; }
.pmodal { position: fixed; inset: 0; background: rgba(0,0,0,.9);
  display: flex; align-items: center; justify-content: center; z-index: 90; }
.pbox { width: min(1000px, 94vw); }
.pname { display: flex; align-items: center; justify-content: space-between;
  color: var(--text); font-size: 13px; padding-bottom: 8px; }
</style>
