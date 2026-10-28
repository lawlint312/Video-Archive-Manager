// Selection logic for the folder scope picker.
//
// No test runner here on purpose — this is plain ESM, run it with:
//     node tests/scopefilter.test.mjs        (from frontend/)
//
// The case that matters is "全不選 while a filter is on". The picker feeds
// the converter and the duplicate scan, both of which delete or rewrite
// files, and the library is 766 rows across 12 roots — if that button reached
// rows the user could not see, it would silently clear or extend a selection
// nobody reviewed.
import {
  isPicked, togglePath, applyAll, matchesQuery, visibleItems, rootCounts,
} from '../src/scopefilter.js'

let failed = 0

function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) {
    console.log(`  OK   ${name}`)
  } else {
    console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`)
    failed++
  }
}

const chars = [
  { name: '七瀬みく', path: 'E:\\JAV\\七瀬みく', root_label: 'E:\\JAV' },
  { name: '七瀬みく', path: 'G:\\JAV\\七瀬みく', root_label: 'G:\\JAV' },
  { name: '七瀬みく', path: 'H:\\JAV\\七瀬みく', root_label: 'H:\\JAV' },
  { name: '森田花梨', path: 'E:\\JAV\\森田花梨', root_label: 'E:\\JAV' },
  { name: 'Amiechikin', path: '\\\\nas\\Social\\Amiechikin',
    root_label: 'NAS\\Social' },
]
const paths = chars.map(c => c.path)
const pickedWith = (flipped, defaultAll) => p => isPicked(flipped, p, defaultAll)

console.log('search matches name and path')
check('by name', chars.filter(c => matchesQuery(c, '森田')).map(c => c.name),
      ['森田花梨'])
check('by drive prefix',
      chars.filter(c => matchesQuery(c, 'E:\\')).map(c => c.path),
      ['E:\\JAV\\七瀬みく', 'E:\\JAV\\森田花梨'])
check('case-insensitive',
      chars.filter(c => matchesQuery(c, 'amie')).map(c => c.name),
      ['Amiechikin'])
check('empty query shows everything',
      chars.filter(c => matchesQuery(c, '   ')).length, 5)

console.log('\nroot chips')
check('counted and sorted by size', rootCounts(chars),
      [{ label: 'E:\\JAV', n: 2 }, { label: 'G:\\JAV', n: 1 },
       { label: 'H:\\JAV', n: 1 }, { label: 'NAS\\Social', n: 1 }])

console.log('\ndefaultAll=true (duplicate scan): everything starts selected')
{
  let flipped = new Set()
  check('all picked', paths.filter(pickedWith(flipped, true)).length, 5)
  flipped = togglePath(flipped, 'E:\\JAV\\森田花梨')
  check('toggling one drops it',
        paths.filter(pickedWith(flipped, true)).length, 4)
  check('and it is the right one',
        isPicked(flipped, 'E:\\JAV\\森田花梨', true), false)
}

console.log('\ndefaultAll=false (converter): nothing starts selected')
{
  let flipped = new Set()
  check('none picked', paths.filter(pickedWith(flipped, false)).length, 0)
  flipped = togglePath(flipped, 'G:\\JAV\\七瀬みく')
  check('toggling one adds it',
        paths.filter(pickedWith(flipped, false)), ['G:\\JAV\\七瀬みく'])
}

console.log('\n*** 全選/全不選 must not reach rows that are filtered out ***')
{
  // converter shape: start empty, select everything on E: only
  let flipped = new Set()
  const onE = visibleItems(chars, { rootFilter: 'E:\\JAV',
                                    picked: pickedWith(flipped, false) })
  check('filter narrowed the view', onE.length, 2)
  flipped = applyAll(flipped, onE, true, false)
  check('selected exactly the shown rows',
        paths.filter(pickedWith(flipped, false)),
        ['E:\\JAV\\七瀬みく', 'E:\\JAV\\森田花梨'])

  // now switch the filter and clear — the E: picks must survive
  const onG = visibleItems(chars, { rootFilter: 'G:\\JAV',
                                    picked: pickedWith(flipped, false) })
  flipped = applyAll(flipped, onG, false, false)
  check('clearing another root left E: alone',
        paths.filter(pickedWith(flipped, false)),
        ['E:\\JAV\\七瀬みく', 'E:\\JAV\\森田花梨'])
}
{
  // duplicate-scan shape: everything on, deselect just what a search shows
  let flipped = new Set()
  const hits = visibleItems(chars, { q: '森田',
                                     picked: pickedWith(flipped, true) })
  check('search narrowed the view', hits.length, 1)
  flipped = applyAll(flipped, hits, false, true)
  check('only the searched row was dropped',
        paths.filter(pickedWith(flipped, true)).length, 4)
  check('the dropped one is right',
        isPicked(flipped, 'E:\\JAV\\森田花梨', true), false)
}

console.log('\n"only selected" view')
{
  let flipped = new Set()
  flipped = applyAll(flipped, [chars[0], chars[3]], true, false)
  const picked = pickedWith(flipped, false)
  check('shows just the picks',
        visibleItems(chars, { onlySelected: true, picked }).map(c => c.path),
        ['E:\\JAV\\七瀬みく', 'E:\\JAV\\森田花梨'])
  check('combines with the root filter',
        visibleItems(chars, { onlySelected: true, rootFilter: 'G:\\JAV', picked })
          .length, 0)
  check('combines with search',
        visibleItems(chars, { onlySelected: true, q: '七瀬', picked })
          .map(c => c.path), ['E:\\JAV\\七瀬みく'])
}

console.log('\ninbox rows ignore the root filter (they have no root)')
{
  const inbox = [{ name: 'C:\\Downloads', path: 'C:\\Downloads' }]
  const picked = () => true
  check('still shown while a root chip is active',
        visibleItems(inbox, { rootFilter: 'E:\\JAV', picked, useRoot: false })
          .length, 1)
}

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
