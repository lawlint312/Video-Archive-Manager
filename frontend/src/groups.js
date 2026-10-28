// Which sidebar/home category sections are open.
//
// Shared between the sidebar and the home grid on purpose: "分類" is one idea
// to the user, so collapsing JAV in one place collapses it in the other.
//
// Sections start closed. What gets stored is therefore the set of *open* ones
// — the deviation from the default — so a category added later inherits the
// default instead of behaving unpredictably because it is missing from an old
// saved list. (Same reasoning as ScopePicker's `flipped`.)
//
// Why closed by default matters here: the library is 619 characters and 465
// of them are 未分類, so the home grid was rendering ~620 avatar cards, three
// quarters of which nobody had asked to see.
import { reactive } from 'vue'
// Explicit extension, like videosearch.js: tests/groups.test.mjs loads this
// module straight from node, where Vite's extensionless resolution does not
// apply. Vite is happy with the full name either way.
import { api } from './api.js'

const LS_KEY = 'vam.groups.open'

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) || '[]')
    return Array.isArray(v) ? v : []
  } catch { return [] }
}

export const groupState = reactive({ open: new Set(load()) })

function persist() {
  localStorage.setItem(LS_KEY, JSON.stringify([...groupState.open]))
}

export function isOpen(name) {
  return groupState.open.has(name)
}

export function toggleGroup(name) {
  const s = new Set(groupState.open)
  s.has(name) ? s.delete(name) : s.add(name)
  groupState.open = s
  persist()
}

/** Force a section open — used to reveal a search hit or the current character. */
export function openGroup(name) {
  if (!name || groupState.open.has(name)) return
  const s = new Set(groupState.open)
  s.add(name)
  groupState.open = s
  persist()
}

export function setAllGroups(names, open) {
  groupState.open = open ? new Set(names) : new Set()
  persist()
}

// ── membership and per-group display settings ────────────────────────────────
//
// The sidebar and the home grid each used to fetch /api/groups into their own
// refs, which was fine while the answer only drove their own rendering. It no
// longer is: App.vue has to know which group the open character belongs to
// before it can decide which tabs to draw, and three components holding three
// copies of that would drift the moment one of them saved.
export const groupData = reactive({
  groups: {},     // { group: [charName, ...] }
  order: [],
  view: {},       // { group: {videos, profile, ratio, poster} } — deviations only
  ready: false,
})

export function applyGroups(d) {
  groupData.groups = d.groups || {}
  groupData.order = d.order || []
  groupData.view = d.view || {}
  groupData.ready = true
}

export async function loadGroups() {
  try {
    applyGroups(await api('/api/groups'))
  } catch { /* the sidebar still renders; everything falls back to defaults */ }
}
