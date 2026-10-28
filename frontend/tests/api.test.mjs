// The fetch wrapper's error translation.
//
//     node tests/api.test.mjs        (from frontend/)
//
// A missing endpoint used to surface as "Not Found", which reads like a bad
// file path — a new feature looked broken when the backend simply had not
// been restarted. Everything else must pass through untouched.

let failed = 0
function check(name, got, want) {
  const ok = got === want
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${name}: ${JSON.stringify(got)}`
    + (ok ? '' : `  (expected ${JSON.stringify(want)})`))
  if (!ok) failed++
}

// stub fetch:每個案例回傳指定的狀態與 detail
function stub(status, body) {
  globalThis.fetch = async () => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
  })
}

const { api, errText } = await import('../src/api.js')

async function detailOf(path, status, body) {
  stub(status, body)
  try { await api(path); return null } catch (e) { return e.detail }
}

console.log("FastAPI's default for a route that does not exist")
check('names the route and says what to do',
      await detailOf('/api/sheet/info?path=x', 404, { detail: 'Not Found' }),
      '執行中的後端沒有 /api/sheet/info 這個功能 —— 請重新啟動後端（stop.bat 然後 start.bat）')

console.log('\nour own errors are left exactly as they are')
// every 404 this app raises is worded differently on purpose; none of them
// may be swallowed by the special case above
for (const d of ['not found', 'job not found', 'character not found',
                 '無法產生縮圖表', 'thumbnail unavailable']) {
  check(`404 "${d}"`, await detailOf('/api/x', 404, { detail: d }), d)
}
check('403', await detailOf('/api/x', 403, { detail: 'path not allowed' }),
      'path not allowed')
check('500', await detailOf('/api/x', 500, { detail: '爆炸了' }), '爆炸了')

console.log('\nedge cases')
// used to resolve to `undefined`, which callers then printed verbatim
check('a 404 with no detail at all',
      await detailOf('/api/x', 404, {}), '伺服器回應 404')
check('a body that is not JSON at all',
      await detailOf('/api/x', 502, null), '伺服器回應 502')
// only 404 means "no such route" — a 500 saying Not Found is a real error
check('"Not Found" on a 500 is left alone',
      await detailOf('/api/x', 500, { detail: 'Not Found' }), 'Not Found')

console.log('\nthe request never completing at all')
// fetch REJECTS here rather than returning a response — backend restarting,
// proxy unable to connect, page navigating away. The bare TypeError it throws
// has no status and no detail, which is how 轉檔失敗：undefined happened.
globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
{
  let e
  try { await api('/api/convert/run') } catch (x) { e = x }
  check('says the backend is unreachable', e.detail,
        '無法連線到後端 —— 可能正在重新啟動，請稍候再試')
  check('status 0 marks it as never having reached the server', e.status, 0)
  check('keeps the underlying reason', e.cause, 'Failed to fetch')
}

console.log('\nerrText never returns the string undefined')
check('api error', errText({ status: 500, detail: '爆炸了' }), '爆炸了')
check('a real JS error', errText(new TypeError('x is not a function')),
      'x is not a function')
check('status only', errText({ status: 503 }), 'HTTP 503')
check('a thrown string', errText('壞了'), '壞了')
check('null', errText(null), '未知的錯誤')
check('an empty object — the case that printed undefined', errText({}),
      '未知的錯誤')

console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
