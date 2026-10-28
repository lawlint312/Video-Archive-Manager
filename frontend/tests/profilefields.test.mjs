// Formatting the javdatabase profile block.
//
//     node tests/profilefields.test.mjs        (from frontend/)
//
// Dates are the whole risk here: javdatabase supplies plenty of year-only
// birthdays, and an age is derived on every render rather than stored,
// because a stored one is wrong within a year.
import {
  parseDob, formatDob, formatAge, profileCells,
} from '../src/profilefields.js'

let failed = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) console.log(`  OK   ${name}`)
  else { console.log(`  FAIL ${name}\n         got ${g}\n    expected ${w}`); failed++ }
}

const NOW = new Date(2026, 7, 1)        // 2026-08-01

console.log('year-only dates show the year alone')
check('2000-??-??', formatDob('2000-??-??'), '2000')
check('full date survives', formatDob('1995-03-29'), '1995-03-29')
check('month known, day not', formatDob('1995-03-??'), '1995')
check('no year at all', formatDob('????-??-??'), '')
check('not a date', formatDob('soon'), '')
check('empty', formatDob(''), '')

console.log('\nage is derived')
check('birthday already passed', formatAge('1995-03-29', NOW), '31 歲')
check('birthday still to come', formatAge('1995-11-15', NOW), '30 歲')
check('birthday is today', formatAge('1995-08-01', NOW), '31 歲')
check('birthday tomorrow', formatAge('1995-08-02', NOW), '30 歲')
check('year only is approximate', formatAge('2000-??-??', NOW), '約 26 歲')
check('unknown', formatAge('????-??-??', NOW), '')

console.log('\nbad dates must not produce a confident age')
check('month 13 / day 40', formatAge('2000-13-40', NOW), '約 26 歲')
check('29 Feb in a common year', formatAge('2001-02-29', NOW), '約 25 歲')
check('year in the future', formatAge('2099-01-01', NOW), '')
check('year too far back', formatAge('1800-01-01', NOW), '')

console.log('\nstat row')
{
  const cells = profileCells({
    dob: '2000-??-??', height: '153 cm', measurements: '88-57-82',
    cup: 'F',
  }, NOW)
  // age lives inside the birthday cell — one derived fact, one box — but as
  // a separate `note` so it can be styled down; pasted into `value` it read
  // as loud as the date
  check('dob shortened, age alongside as a note',
        cells[0], { key: 'dob', label: '生日', value: '2000', note: '約 26 歲' })
  check('age is not a cell of its own', cells.some(c => c.key === 'age'), false)
  check('only the birthday carries a note',
        cells.filter(c => c.note).map(c => c.key), ['dob'])
  // the site's own Tags field was dropped — vague and near-identical
  // between performers, so it earned no space
  check('site tags never appear', cells.some(c => c.key === 'tags'), false)
  check('missing fields omitted', cells.map(c => c.key),
        ['dob', 'height', 'measurements', 'cup'])
}
// NOW is 2026-08-01, so this birthday is still a fortnight away — 29, not 30
check('full date keeps both halves', profileCells({ dob: '1996-08-15' }, NOW)[0],
      { key: 'dob', label: '生日', value: '1996-08-15', note: '29 歲' })
// an unreadable birthday must not leave an empty bracket behind
check('junk dob produces no cell', profileCells({ dob: 'soon' }, NOW), [])
check('dob missing, other fields still shown',
      profileCells({ cup: 'F' }, NOW).map(c => c.key), ['cup'])
check('nothing in, nothing out', profileCells(null), [])
check('empty profile', profileCells({}), [])

console.log('\nparse details')
check('valid full', parseDob('1995-03-29'), { y: 1995, mo: 3, d: 29 })
check('leap day in a leap year', parseDob('2000-02-29'), { y: 2000, mo: 2, d: 29 })
check('bad day falls back to the year',
      parseDob('2001-02-29'), { y: 2001, mo: null, d: null })

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
