// How a film relates to others, in words.
//   node tests/relinfo.test.mjs
import { relVerb, relSummary, relLabel } from '../src/relinfo.js'

let failed = 0
const check = (ok, msg) => { console.log((ok ? '  ok   ' : '  FAIL ') + msg); if (!ok) failed++ }

check(relVerb('original', false) === '擷取自' && relVerb('original', true) === '被擷取成',
      '同一筆關係從兩端讀：AKO-599 擷取自 UFD-061、UFD-061 被擷取成 AKO-599')
check(relVerb('digest', false) === '被選入精選集' && relVerb('digest', true) === '片段來自',
      '精選總集編：UFD-061 被選入精選集 VRKM-1814、VRKM-1814 片段來自 UFD-061')
check(relVerb('uncut', false) === '完整收錄於' && relVerb('uncut', true) === '完整收錄',
      '完整收錄的總集編：XVSR-449 完整收錄於 MBF-037、MBF-037 完整收錄 XVSR-449')
check(relVerb('weird', false) === '收錄於', '不認得的判斷也有一句話')

check(relSummary([{ o: 'UFD-061', k: 'original', p: false }]) === '擷取自 UFD-061', '一筆')
check(relSummary([{ o: 'AKO-385', k: 'original', p: true }, { o: 'AKO-599', k: 'original', p: true },
                  { o: 'BEST-1', k: 'unknown', p: false }])
      === '收錄於 BEST-1 · 被擷取成 2 部',
      '自己的來源排前面，同一種的合成「N 部」')
check(relSummary([]) === '' && relSummary(null) === '', '沒有就是空字串')

check(relLabel([{ o: 'UFD-061', k: 'original', p: false }]) === 'UFD-061', '卡片標記：對方的番號')
check(relLabel([{ o: 'AKO-385', k: 'original', p: true }, { o: 'UFD-001', k: 'original', p: false },
                { o: 'AKO-599', k: 'original', p: true }]) === 'UFD-001 +2',
      '好幾筆：先顯示它的來源，其餘算成 +N')
check(relLabel([]) === '' && relLabel(undefined) === '', '沒有關係就沒有標記')

console.log(failed ? `\n${failed} FAILED` : '\nPASS')
process.exit(failed ? 1 : 0)
