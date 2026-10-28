import { passwordProblem, changeProblem } from '../src/password.js'

let fail = 0
const check = (ok, msg) => { console.log((ok ? '  ok   ' : '  FAIL ') + msg); if (!ok) fail++ }

check(passwordProblem('Abc12') === '密碼至少 8 個字元', '太短')
check(passwordProblem('abcdefgh1').includes('大寫'), '缺大寫')
check(passwordProblem('Abcdefgh1') === null, '符合規則')
check(passwordProblem(undefined) === '密碼至少 8 個字元', '沒有值不會當掉')

check(changeProblem('', 'NewPass456', 'NewPass456') === '請輸入目前的密碼', '沒填目前密碼')
check(changeProblem('Old12345A', 'short', 'short').includes('8'), '新密碼不符規則')
check(changeProblem('Old12345A', 'NewPass456', 'NewPass457') === '兩次輸入的新密碼不一樣', '兩次不一致')
check(changeProblem('NewPass456', 'NewPass456', 'NewPass456').includes('相同'), '新舊相同')
check(changeProblem('Old12345A', 'NewPass456', 'NewPass456') === null, '全部正確')

console.log(fail ? `${fail} 項失敗` : '全部通過')
process.exit(fail ? 1 : 0)
