import { reactive } from 'vue'
import { clearVideoIndex } from './videosearch'

// Global app state.
//   user         : { id, username, role, role_name, perms, can_edit } | null
//   editMode     : editor convenience toggle (edit tools vs browse-only)
//   selectedChar : { name, path } | null  (null = home view)
//   activeTab    : 'videos' | 'profile'
//   defaultTab   : configured default tab shown when a character is opened
//   seekSeconds  : arrow-key seek step for the video player
// "May this account do any of these?" The server enforces the same thing on
// every endpoint (auth.require_perm); this only decides which buttons exist.
export function can(...perms) {
  const have = store.user?.perms || []
  return perms.some(p => have.includes(p))
}

export const store = reactive({
  user: null,
  // One line for the login page, e.g. after changing your own password signs
  // you out. Deliberately not cleared by resetUserState — that runs exactly
  // when this message is needed.
  loginNotice: '',
  // Bumped when a tool has changed files under the current listing (a convert
  // run); the video grid lists again so it sees the new mtimes.
  filesVersion: 0,
  // Bumped when a tool has changed characters' avatars (角色查詢 → 個資 with a
  // Gfriends picture); the home grid and the sidebar list them again.
  charsVersion: 0,
  editMode: false,
  selectedChar: null,
  activeTab: 'videos',
  defaultTab: 'videos',
  seekSeconds: 10,
  // Deliberately a second gate inside edit mode: deleting is irreversible, so
  // it should never be one stray click away. Reset on any navigation.
  deleteMode: false,
  // Clip mode is not a safety gate — cutting creates a file and touches
  // nothing else. It is a mode only so the card corner is not permanently
  // crowded with buttons. Reset alongside deleteMode for the same reason:
  // a mode you left switched on in another character is a surprise.
  clipMode: false,
  view: 'home',        // 'home' | 'inbox' | 'char'
  hasInbox: false,     // inbox folders configured in settings?
  faceAvailable: false,  // GPU + deps present → face recognition enabled
  faceReason: '',        // why it's unavailable
  // status bar
  status: '',            // last completed action (persistent)
  statusKind: 'info',    // info | ok | warn
  busyText: '',          // background work in progress (face index, dedup…)
  // set by a sidebar search hit: VideoGrid scrolls to this path and flashes
  // it, then clears it. Without it, jumping to a character with 300 videos
  // leaves you to find the one you searched for by hand.
  focusFile: null,
  // set by clicking a tag on a profile: the home grid shows only the
  // characters carrying it. A tag you cannot follow is decoration -- which is
  // measurably what these had become (44 tags, not one of them reused).
  tagFilter: null,
  // session archive log
  archived: [],          // [{ name, person, at }]
  // duplicate scan — lives here (not in the dialog) so closing the dialog does
  // not abandon the scan, and reopening it shows the report again.
  dedup: {
    jobId: null,
    running: false,
    stopping: false,     // stop requested; the current file still finishes
    // structured: { phase, phases, label, done, total, name, hint, eta }
    progress: null,
    groups: null,        // null = never scanned
    tier2: false,        // was Tier 2 part of the scan behind these groups?
    savedAt: null,       // epoch of the scan, when restored from disk
    partial: false,      // report came from a scan that was stopped early
    historyId: null,     // which saved scan is on screen (null = the latest)
    error: '',
  },
  // the same again for images; a separate slice because both scans can be
  // sitting on an unfinished report at once
  imgdedup: {
    jobId: null,
    running: false,
    stopping: false,
    progress: null,
    groups: null,
    savedAt: null,
    partial: false,
    maxDistance: 20,     // out of 256; the server owns the real default
    safeDistance: 14,    // below this, matches were measured to be reliable
    historyId: null,
    error: '',
  },
  // The convert scan being followed (convertscan.js). Out here, not in the
  // dialog, for the same reason as dedup: closing the dialog must not stop
  // the status bar, and reopening it must find the same scan.
  convertScan: {
    jobId: null,
    options: null,       // what that scan checks, for the dialog's buttons
    progress: '',
    stopping: false,
    result: null,        // arrived while the dialog was closed: shown on open
    error: '',
  },
})

/** Wipe everything that belonged to the previous account.
 *  Signing in as someone else in the same tab must not inherit their config,
 *  their cached numbers, or their view state. */
export function resetUserState() {
  store.editMode = false
  store.deleteMode = false
  store.clipMode = false
  store.selectedChar = null
  store.activeTab = 'videos'
  store.defaultTab = 'videos'
  store.seekSeconds = 10
  store.view = 'home'
  store.hasInbox = false
  store.faceAvailable = false
  store.faceReason = ''
  store.status = ''
  store.statusKind = 'info'
  store.busyText = ''
  store.focusFile = null
  store.tagFilter = null
  store.archived = []
  clearVideoIndex()
  Object.assign(store.dedup,
    { jobId: null, running: false, stopping: false, progress: null,
      groups: null, tier2: false, savedAt: null, partial: false,
      historyId: null, error: '' })
  Object.assign(store.imgdedup,
    { jobId: null, running: false, stopping: false, progress: null,
      groups: null, savedAt: null, partial: false, maxDistance: 20,
      safeDistance: 14, historyId: null, error: '' })
  Object.assign(store.convertScan,
    { jobId: null, options: null, progress: '', stopping: false,
      result: null, error: '' })
}

export function setStatus(msg, kind = 'ok') {
  store.status = msg
  store.statusKind = kind
}
export function setBusy(text) { store.busyText = text }
export function logArchived(name, person) {
  store.archived.unshift({ name, person, at: Date.now() })
}

// Player keyboard shortcuts live in ./player.js (document-level listener).
