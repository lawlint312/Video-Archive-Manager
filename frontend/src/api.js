/**
 * Turn a useless server error into an actionable one.
 *
 * FastAPI answers a route it does not have with exactly `{"detail":"Not
 * Found"}`. Every 404 this app raises itself is worded differently (checked:
 * "not found", "job not found", "無法產生縮圖表"…), so that exact capitalised
 * string means the endpoint is missing from the *running* server — which in
 * practice always means the backend predates the frontend.
 *
 * Worth special-casing because the raw message actively misleads: a new
 * feature reports "Not Found" and reads like a broken file path.
 */
function explain(path, status, detail) {
  if (status === 404 && detail === 'Not Found') {
    const route = String(path).split('?')[0]
    return `執行中的後端沒有 ${route} 這個功能 —— 請重新啟動後端（stop.bat 然後 start.bat）`
  }
  return detail
}

/** Text for any thrown value, never the string "undefined".
 *
 *  Callers used to write `e.detail || e.status`, which reads fine until
 *  the thrown thing is a plain JavaScript error — a real `TypeError` has neither
 *  field, so the user was shown "轉檔失敗：undefined" and nobody, including
 *  the log, could say what had happened. */
export function errText(e) {
  if (e == null) return '未知的錯誤'
  if (typeof e === 'string') return e
  return e.detail || e.message || (e.status ? `HTTP ${e.status}` : '未知的錯誤')
}

// Thin fetch wrapper. Always sends the session cookie (same-origin via Vite proxy).
export async function api(path, opts = {}) {
  let r
  try {
    // FormData has to set its own Content-Type, because the multipart
    // boundary is generated per request and lives in that header. Forcing
    // application/json onto it produces a body the server cannot parse and
    // an error that says nothing about the real cause.
    const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData
    r = await fetch(path, {
      credentials: 'include',
      headers: isForm ? (opts.headers || {})
                      : { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    })
  } catch (e) {
    // fetch rejects (rather than returning a response) when the request never
    // completed: backend down or restarting, the dev-server proxy unable to
    // connect, the page navigating away mid-request. It throws a bare
    // TypeError with no status, which is how "undefined" reached the screen.
    throw { status: 0, detail: '無法連線到後端 —— 可能正在重新啟動，請稍候再試',
            cause: e.message }
  }
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail } catch { /* not JSON */ }
    // Never hand back an empty detail: some errors (a proxy failure, a 502)
    // have no JSON body at all, and `detail || status` would then show a bare
    // number with no clue what it was about.
    throw { status: r.status,
            detail: explain(path, r.status, detail) || `伺服器回應 ${r.status}` }
  }
  const ct = r.headers.get('content-type') || ''
  return ct.includes('application/json') ? r.json() : r
}

// Build media URLs (browser hits the Vite proxy, which forwards to the backend).
// `poster` asks the server to prefer a same-named image sitting beside the
// video. Opt-in per call, not a default: the picture then comes from a
// different file, which is right for a card and wrong for the compare view.
// `ver` (the file's mtime) is only for the browser: a thumbnail is cached for
// 30 days under its URL, and a file replaced in place by a convert or repair
// keeps its path — without this the card would show the old picture for a
// month. The server ignores it.
// `portrait` (a 4:5 / 9:16 card) asks for the portrait cover (`-poster`, or a
// sleeve's front); otherwise the landscape `-fanart` is tried first. Only
// meaningful with `poster`. See media.poster_candidates.
//
// POSTER_GEN: the *rule* that picks the cover is not in the file's mtime, so
// a change of rule kept the old picture in every browser for a month — 三浦真央
// kept showing the portrait `-poster` after 16:9 cards switched to `-fanart`,
// because both files dated from 2023 and the URL did not change. Bump it
// whenever media.poster_candidates or the crop changes. 2: -fanart for 16:9.
const POSTER_GEN = 2
export const thumbUrl = (p, pct = 0.1, size = 640, poster = false, ver = 0,
                         portrait = false) =>
  `/api/thumb?path=${encodeURIComponent(p)}&pct=${pct}&size=${size}` +
  (poster ? `&poster=1&pg=${POSTER_GEN}` : '') +
  (poster && portrait ? '&shape=portrait' : '') +
  (ver ? `&v=${Math.floor(ver)}` : '')
export const streamUrl = (p) => `/api/stream?path=${encodeURIComponent(p)}`
// WebVTT converted from the video's sidecar subtitle file. 404 when there is
// none, which is the usual answer — callers check before drawing a <track>.
export const subtitleUrl = (p) => `/api/subtitle?path=${encodeURIComponent(p)}`
export const imageUrl = (p) => `/api/image?path=${encodeURIComponent(p)}`
// For grids. The cell is 280px wide but the sources are phone-camera
// originals (3024x4032), so a screenful was 43.9 MB and 333 megapixels of
// decoding. Full size stays for the viewer and the avatar cropper, where the
// pixels are actually looked at.
//
// The response is cached by the browser for 30 days and the URL only changes
// with the path, so a thumbnail made wrong stays wrong on every device even
// after the server makes it right. Bump THUMB_GEN whenever the way photothumb
// draws a picture changes. 2: damaged progressive JPEGs no longer come out
// solid black (2026-10-27).
const THUMB_GEN = 2
export const imageThumbUrl = (p) =>
  `/api/image/thumb?path=${encodeURIComponent(p)}&g=${THUMB_GEN}`
