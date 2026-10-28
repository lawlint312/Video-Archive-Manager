<script setup>
/**
 * The frame shared by the dialogs that have several pages: the categories
 * down the left, the chosen one's title and content on the right.
 *
 * Settings, 重複掃描, 影片處理 and 角色查詢 each used to draw their own header
 * and tab strip, four slightly different ways. Only the frame is shared —
 * every page keeps its own content, scrolling and state.
 *
 * `locked` is for a run that closing would abandon (角色查詢's scans, a
 * convert in progress): the close button, the backdrop and switching pages
 * all stop working while it is set. 重複掃描 never sets it — its scans run on
 * in the background on purpose (see DupDialog).
 */
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  title: { type: String, required: true },   // the dialog's name, top left
  // [{ key, label, icon, badge, warn, spinning, note, heading }] — `heading`
  // is a longer page title when the label has to stay short for the column
  items: { type: Array, required: true },
  modelValue: { type: String, required: true },
  subtitle: { type: String, default: '' },   // under the page title
  locked: { type: Boolean, default: false },
  width: { type: String, default: '940px' },
  height: { type: String, default: '720px' },
  z: { type: Number, default: 60 },
  // the page title's side padding: match the content's, so they line up
  pad: { type: String, default: '24px' },
})
const emit = defineEmits(['update:modelValue', 'close'])

const active = computed(() =>
  props.items.find(n => n.key === props.modelValue) || props.items[0])
</script>

<template>
  <div class="modal" :style="{ zIndex: z }" @click.self="!locked && emit('close')">
    <div class="sdlg" :style="{ '--w': width, '--h': height, '--pad': pad }">
      <nav class="side">
        <div class="sidehead">{{ title }}</div>
        <button v-for="n in items" :key="n.key" class="nav ib"
                :class="{ on: modelValue === n.key }"
                :disabled="locked && modelValue !== n.key"
                @click="emit('update:modelValue', n.key)">
          <Icon :name="n.spinning ? 'spinner' : n.icon" :size="16"
                :class="{ spin: n.spinning }" />
          <span class="navlabel">{{ n.label }}</span>
          <span v-if="n.note" class="navnote">{{ n.note }}</span>
          <span v-else-if="n.badge" class="navbadge" :class="{ warn: n.warn }">{{ n.badge }}</span>
        </button>
      </nav>

      <section class="pane">
        <div class="phead">
          <div class="ptitle">
            <h2>{{ active?.heading || active?.label }}</h2>
            <div v-if="subtitle" class="psub">{{ subtitle }}</div>
          </div>
          <button class="xbtn" title="關閉" :disabled="locked" @click="emit('close')">
            <Icon name="close" :size="16" /></button>
        </div>
        <!-- a bounded flex column: each page puts its own scroller in here -->
        <div class="pbody"><slot /></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.8);
  display: flex; align-items: center; justify-content: center; }
/* A fixed height, so switching pages does not make the dialog jump */
.sdlg { background: var(--panel); border-radius: 14px; width: min(var(--w), 96vw);
  height: min(var(--h), 90vh); height: min(var(--h), 90dvh); display: flex;
  overflow: hidden; box-shadow: 0 20px 60px #0008; }
.side { width: 200px; flex: 0 0 auto; background: var(--bg); padding: 16px 10px;
  display: flex; flex-direction: column; gap: 4px; border-right: 1px solid #313148;
  overflow-y: auto; }
/* the dialog's name: text only, big enough to read as the heading of the
   column (it was 12px and grey, with an icon — the user asked for neither) */
.sidehead { font-size: 17px; font-weight: 600; color: var(--text);
  padding: 4px 10px 12px; }
.nav { width: 100%; gap: 10px; padding: 9px 10px; border: 1px solid transparent;
  border-radius: 9px; background: none; color: var(--dim); font: inherit;
  font-size: 13px; cursor: pointer; text-align: left; }
.nav:hover:not(:disabled) { color: var(--text); background: #ffffff0a; }
.nav.on { color: var(--text); background: var(--card); border-color: #ffffff14; }
.nav:disabled { opacity: .4; cursor: default; }
.navlabel { flex: 1; }
.navbadge { background: #ffffff1f; border-radius: 8px; padding: 0 6px;
  font-size: 10px; font-weight: bold; }
.navbadge.warn { background: var(--danger); color: #1e1e2e; }
.navnote { font-size: 10px; opacity: .8; }
.spin { animation: sdspin 1s linear infinite; }
@keyframes sdspin { to { transform: rotate(360deg); } }
.pane { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.phead { display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 18px var(--pad) 10px; flex: 0 0 auto; }
.ptitle { min-width: 0; }
.phead h2 { margin: 0; font-size: 20px; font-weight: 600; }
.psub { color: var(--dim); font-size: 11px; margin-top: 3px; }
.xbtn { width: 32px; height: 32px; flex: 0 0 auto; border-radius: 50%;
  border: 1px solid #ffffff1f; background: none; color: var(--dim); display: flex;
  align-items: center; justify-content: center; cursor: pointer; }
.xbtn:hover:not(:disabled) { color: var(--text); background: #ffffff0f; }
.xbtn:disabled { opacity: .4; cursor: default; }
.pbody { flex: 1; min-height: 0; display: flex; flex-direction: column; }
/* Narrow windows (a tablet held upright): the pages become a strip above
   the content. Phones never open these dialogs — their buttons are
   .no-mobile. */
@media (max-width: 720px) {
  .sdlg { flex-direction: column; height: 92vh; height: 92dvh; }
  .side { width: auto; flex-direction: row; overflow-x: auto; padding: 8px;
    border-right: none; border-bottom: 1px solid #313148; }
  .sidehead { display: none; }
  .nav { width: auto; flex: 0 0 auto; }
  .phead { padding: 14px 16px 8px; }
}
</style>
