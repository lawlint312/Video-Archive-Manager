// The password rules, as the browser shows them. The server checks the same
// rules again (security.password_problem) — this copy only exists so the
// message appears before a round trip. Shared by the account manager and the
// change-my-password dialog so the two can never disagree.

export const POLICY = '密碼至少 8 個字元，且必須包含大寫英文、小寫英文與數字'

/** null if acceptable, else a message saying exactly what is missing. */
export function passwordProblem(pw) {
  pw = pw || ''
  if (pw.length < 8) return '密碼至少 8 個字元'
  const missing = []
  if (!/[a-z]/.test(pw)) missing.push('小寫英文字母')
  if (!/[A-Z]/.test(pw)) missing.push('大寫英文字母')
  if (!/[0-9]/.test(pw)) missing.push('數字')
  return missing.length ? '密碼還缺少：' + missing.join('、') : null
}

/** First thing wrong with the change-my-password form, or null. */
export function changeProblem(current, next, again) {
  if (!current) return '請輸入目前的密碼'
  const p = passwordProblem(next)
  if (p) return p
  if (next !== again) return '兩次輸入的新密碼不一樣'
  if (next === current) return '新密碼不能和目前的密碼相同'
  return null
}
