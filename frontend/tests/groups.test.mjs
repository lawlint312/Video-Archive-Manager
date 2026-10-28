// Category open/closed state, shared by the sidebar and the home grid.
//
//     node tests/groups.test.mjs        (from frontend/)
//
// Sections default to closed, so what is stored is the set of *open* ones.
// The case worth guarding is a category added later: it must inherit the
// default rather than pick up whatever an old saved list implies.

const store = new Map()
globalThis.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
  clear: () => store.clear(),
}

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

const LS = 'vam.groups.open'
const G = await import('../src/groups.js')

console.log('everything starts closed')
check('nothing open', ['JAV', '未分類'].filter(G.isOpen), [])

console.log('\ntoggle')
G.toggleGroup('JAV')
check('opened', G.isOpen('JAV'), true)
check('others untouched', G.isOpen('未分類'), false)
check('persisted', JSON.parse(localStorage.getItem(LS)), ['JAV'])
G.toggleGroup('JAV')
check('closed again', G.isOpen('JAV'), false)
check('persisted', JSON.parse(localStorage.getItem(LS)), [])

console.log('\nopenGroup (used to reveal a search hit / the current character)')
G.openGroup('WEB CAM')
check('opened', G.isOpen('WEB CAM'), true)
G.openGroup('WEB CAM')
check('idempotent', JSON.parse(localStorage.getItem(LS)), ['WEB CAM'])
G.openGroup('')
check('ignores an empty name', JSON.parse(localStorage.getItem(LS)), ['WEB CAM'])

console.log('\nexpand all / collapse all')
const names = ['JAV', 'Social Media 歐美', 'WEB CAM', '未分類']
G.setAllGroups(names, true)
check('all open', names.every(G.isOpen), true)
G.setAllGroups(names, false)
check('all closed', names.some(G.isOpen), false)

console.log('\na category added later inherits the default')
G.setAllGroups(names, true)
check('a brand new name is closed, not open', G.isOpen('新分類'), false)

console.log('\nstate survives a reload')
{
  G.setAllGroups(['JAV'], true)
  const fresh = await import('../src/groups.js?reload=1')
  check('reads back what was stored', fresh.isOpen('JAV'), true)
  check('and only that', fresh.isOpen('未分類'), false)
}

console.log('\ncorrupt storage does not break startup')
{
  localStorage.setItem(LS, 'not json at all')
  const fresh = await import('../src/groups.js?reload=2')
  check('falls back to all closed', fresh.isOpen('JAV'), false)
  localStorage.setItem(LS, '{"not":"an array"}')
  const fresh2 = await import('../src/groups.js?reload=3')
  check('and rejects the wrong shape too', fresh2.isOpen('JAV'), false)
}

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
