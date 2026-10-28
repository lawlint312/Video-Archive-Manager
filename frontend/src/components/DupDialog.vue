<script setup>
/**
 * One dialog for both duplicate hunts.
 *
 * These were two buttons and two dialogs that already shared the scope picker
 * (down to its default of everything-selected), `ScanProgress.vue`, the
 * cooperative stop, and the "report lives on the server, closing does not
 * cancel" contract — `imgdedup.js` even re-exports `fmtSize`/`fmtDate` from
 * `dedup.js`. It is one job done on two kinds of media.
 *
 * ── Deliberately NOT copied from JavDialog ──────────────────────────────────
 *
 * JavDialog disables its close button and its tabs while a panel is busy,
 * because abandoning one of those scans loses it. These scans are the exact
 * opposite: the polling loop lives in `dedup.js`/`imgdedup.js` precisely so
 * that closing the dialog does not abandon a run, and `App.vue` reattaches to
 * a running job after a reload. So nothing here is ever disabled — gating the
 * close button would remove a capability that was built on purpose.
 *
 * The two scans are also independent jobs that can run at the same time, and
 * merging the buttons must not change that. Hence the per-tab spinners rather
 * than one shell-level "busy".
 */
import { ref, computed } from 'vue'
import { store } from '../store'
import VideoDupPanel from './VideoDupPanel.vue'
import ImageDupPanel from './ImageDupPanel.vue'
import RelationDupPanel from './RelationDupPanel.vue'
import SideDialog from './SideDialog.vue'

const props = defineProps({
  // which tab to land on; the header button passes whichever count it showed
  start: { type: String, default: 'video' },
})
const emit = defineEmits(['close'])

const tab = ref(['image', 'relation'].includes(props.start) ? props.start : 'video')

/** Groups still worth a look, per kind — the same counts the two header
 *  buttons used to carry, now on the tabs that can say which is which. */
const videoCount = computed(() => (store.dedup.groups || [])
  .filter(g => g.files.filter(f => !f.deleted).length > 1).length)
const imageCount = computed(() => (store.imgdedup.groups || [])
  .filter(g => g.files.filter(f => !f.deleted).length > 1).length)

// A scan running on the other page has to stay visible from here, because
// both can run at once: a spinner and 掃描中 on its entry, else the count.
const TABS = computed(() => [
  { key: 'video', icon: 'film', label: '影片',
    spinning: store.dedup.running, note: store.dedup.running ? '掃描中' : '',
    badge: videoCount.value },
  { key: 'image', icon: 'image', label: '圖片',
    spinning: store.imgdedup.running, note: store.imgdedup.running ? '掃描中' : '',
    badge: imageCount.value },
  // No scan behind it: AVBase's stored relations against the library index
  { key: 'relation', icon: 'link', label: '同內容不同番號' },
])
</script>

<template>
  <!-- Never locked: these scans carry on in the background on purpose -->
  <SideDialog title="重複掃描" :items="TABS" v-model="tab"
              width="1340px" height="1000px" pad="16px" @close="emit('close')">
    <!-- Kept alive: switching pages must not throw away a report you are
         partway through acting on. Each panel keeps its own scroller — a
         second one here would fight it for height. -->
    <KeepAlive>
      <VideoDupPanel v-if="tab === 'video'" />
      <ImageDupPanel v-else-if="tab === 'image'" />
      <RelationDupPanel v-else />
    </KeepAlive>
  </SideDialog>
</template>
