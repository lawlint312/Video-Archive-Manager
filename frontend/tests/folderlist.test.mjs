// Folder lists in Settings (inbox folders, archive roots).
//
//     node tests/folderlist.test.mjs        (from frontend/)
//
// These replaced a textarea per list. What must not regress: pasting the old
// one-per-line text still works, the same folder is never in a list twice
// (case and trailing separator aside), and the browser's "up" never offers a
// place Windows cannot list (`\\host` alone).
import {
  folderKey, cleanFolder, addFolders, moveFolder, parentFolder, startPlaces,
  joinFolder,
} from '../src/folderlist.js'

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

const NAS = '\\\\192.168.1.x\\share'

check('same folder: case and trailing separator',
  folderKey('D:\\JAV\\'), folderKey('d:\\jav'))
check('drive root keeps its separator', folderKey('D:\\'), 'd:\\')
check('forward slashes count as backslashes',
  folderKey('D:/JAV'), folderKey('D:\\JAV'))

check('quotes from "copy as path" are dropped',
  cleanFolder('"D:\\Videos\\"'), 'D:\\Videos')
check('bare drive becomes its root', cleanFolder('d:'), 'd:\\')
check('drive root stays', cleanFolder('D:\\'), 'D:\\')

let r = addFolders(['D:\\JAV'], `E:\\New\r\n\n  d:\\jav\\  \n${NAS}\\JAV\nE:\\new`)
check('pasting several lines adds each once', r.list,
  ['D:\\JAV', 'E:\\New', `${NAS}\\JAV`])
check('what was already there is reported', r.dupes, ['d:\\jav', 'E:\\new'])
check('what was added is reported', r.added, ['E:\\New', `${NAS}\\JAV`])
check('blank input adds nothing', addFolders(['A:\\x'], '   ').list, ['A:\\x'])

check('move up', moveFolder(['a', 'b', 'c'], 2, -1), ['a', 'c', 'b'])
check('first cannot move up', moveFolder(['a', 'b'], 0, -1), ['a', 'b'])
check('last cannot move down', moveFolder(['a', 'b'], 1, 1), ['a', 'b'])

check('parent of a folder', parentFolder('D:\\JAV\\x'), 'D:\\JAV')
check('parent of a top folder is the drive root', parentFolder('D:\\JAV'), 'D:\\')
check('drive root goes to the drive list', parentFolder('D:\\'), '')
check('parent inside a share', parentFolder(`${NAS}\\JAV\\a`), `${NAS}\\JAV`)
check('share folder goes up to the share', parentFolder(`${NAS}\\JAV`), NAS)
check('a share cannot go up to its host', parentFolder(NAS), '')

check('start places: the folders above, once each',
  startPlaces([`${NAS}\\JAV`, `${NAS}\\Social`, 'C:\\Users\\me\\Downloads']),
  [NAS, 'C:\\Users\\me'])

check('join under a drive root', joinFolder('D:\\', 'JAV'), 'D:\\JAV')
check('join under a share', joinFolder(NAS, 'JAV'), `${NAS}\\JAV`)
check('join from the drive list', joinFolder('', 'D:\\'), 'D:\\')

console.log(failed ? `\n${failed} failed` : '\nall passed')
process.exit(failed ? 1 : 0)
