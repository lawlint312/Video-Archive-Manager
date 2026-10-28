<script setup>
/**
 * One dialog for everything that answers a question about a JAV release.
 *
 * These were two separate buttons and two separate dialogs that shared a
 * character picker, a scanning pattern and a results list — the same
 * relationship 轉檔 and 壓縮 have, and they live in one dialog for the same
 * reason: it is one job ("find out about these films") done from different
 * sources, not three unrelated tools.
 *
 * The panels share only this shell. Their scans, parameters and results are
 * entirely their own, exactly as ConvertDialog and ShrinkPanel are.
 */
import { ref, computed } from 'vue'
import IdolProfilePanel from './IdolProfilePanel.vue'
import MovieTitlePanel from './MovieTitlePanel.vue'
import WikiImportPanel from './WikiImportPanel.vue'
import NfoPanel from './NfoPanel.vue'
import SideDialog from './SideDialog.vue'

const emit = defineEmits(['close'])

const TABS = [
  { key: 'profile', icon: 'user', label: '個資' },
  { key: 'title', icon: 'film', label: '片名' },
  { key: 'wiki', icon: 'package', label: 'WIKI 匯入' },
  { key: 'nfo', icon: 'note', label: 'NFO' },
]
const tab = ref('profile')

// Each panel reports whether it is mid-run; closing during a scan would
// abandon a job the user cannot get back to.
const profileEl = ref(null)
const titleEl = ref(null)
const wikiEl = ref(null)
const nfoEl = ref(null)
const busy = computed(() =>
  !!(profileEl.value?.busy || titleEl.value?.busy || wikiEl.value?.busy
     || nfoEl.value?.busy))
</script>

<template>
  <!-- Locked while a panel is busy: closing or switching mid-scan would
       abandon a job the user cannot get back to. The subtitle says which
       source is answering — the wiki gives original Japanese, javdatabase
       machine-translated English, and nobody should have to remember which
       page is which. -->
  <SideDialog title="角色查詢" :items="TABS" v-model="tab"
              :locked="busy" width="1100px" height="900px" :z="50"
              :subtitle="tab === 'wiki' ? '來源：你存下來的 wiki 頁面'
                         : tab === 'nfo' ? '影片旁的 .nfo（Kodi／Jellyfin／Emby）'
                         : tab === 'title' ? '來源：DMM（日文）、javdatabase（英文）'
                                        : '來源：javdatabase'"
              @close="emit('close')">
    <!-- Kept alive rather than re-created: switching pages mid-scan must not
         throw away a running job or a result list you have not acted on. -->
    <div class="body">
      <KeepAlive>
        <IdolProfilePanel v-if="tab === 'profile'" ref="profileEl" />
        <MovieTitlePanel v-else-if="tab === 'title'" ref="titleEl" />
        <WikiImportPanel v-else-if="tab === 'wiki'" ref="wikiEl" />
        <NfoPanel v-else ref="nfoEl" />
      </KeepAlive>
    </div>
  </SideDialog>
</template>

<style scoped>
/* The scroller. Without `min-height: 0` a flex child refuses to shrink below
   its content and the dialog's height does nothing — the same trap the
   convert and shrink panels were fixed for. */
.body { flex: 1; min-height: 0; overflow-y: auto; padding: 4px 24px 20px;
  display: flex; flex-direction: column; gap: 10px; }
@media (max-width: 720px) {
  .body { padding: 4px 16px 16px; }
}
</style>
